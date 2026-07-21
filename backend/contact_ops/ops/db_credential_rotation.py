#!/usr/bin/env python3
# ruff: noqa: S608 -- SQL interpolation is limited to fixed role names and UUID-validated ids.
"""Stage and execute the Contact-Ops PostgreSQL credential rotation.

The helper is deliberately narrow and deployment-specific.  It knows the
production role split and refuses to operate if the protected environment file
does not match it:

* DATABASE_URL and AUDIT_DATABASE_URL use ``contact_ops_runtime``;
* MIGRATION_DATABASE_URL uses ``contact_ops_admin``; and
* every DSN points at ``contact-ops-postgres:5432/contact_ops_db``.

Secret values are generated with :mod:`secrets`, kept only in mode-0600 files,
and sent to ``psql`` on stdin.  They never enter argv, the process environment,
stdout, or stderr.  PostgreSQL password changes use psql's ``\\password`` meta
command so cleartext is not placed in SQL history or server statement logs.

This module does not stop or start services.  Follow
``docs/DATABASE-CREDENTIAL-ROTATION.md`` for the maintenance-window sequence.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import resource
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Never
from urllib.parse import unquote, urlsplit, urlunsplit

RUNTIME_ROLE: Final = "contact_ops_runtime"
MIGRATION_ROLE: Final = "contact_ops_admin"
AUDIT_POLICY_ROLE: Final = "contact_ops_audit"
APP_POLICY_ROLE: Final = "contact_ops_app"
DB_CONTAINER_DEFAULT: Final = "contact-ops-postgres"
DB_HOST: Final = "contact-ops-postgres"
DB_PORT: Final = 5432
DB_NAME: Final = "contact_ops_db"
PRODUCTION_DB_NETWORK: Final = "unicorn-network"

ENV_KEYS: Final = ("DATABASE_URL", "AUDIT_DATABASE_URL", "MIGRATION_DATABASE_URL")
STATE_FILES: Final = ("previous.env", "runtime.env", "final.env", "manifest.json")
MAX_SECRET_FILE_BYTES: Final = 1024 * 1024

_ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)(?P<value>.*)$"
)
_SAFE_PASSWORD_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class RotationError(RuntimeError):
    """Expected fail-closed error whose text is safe to show an operator."""


@dataclass(frozen=True)
class _Assignment:
    index: int
    prefix: str
    value: str
    quote: str
    newline: str


@dataclass(frozen=True, repr=False)
class _Dsn:
    key: str
    scheme: str
    username: str
    password: str
    hostname: str
    port: int
    database: str


@dataclass(frozen=True)
class _State:
    directory: Path
    source_env: Path
    files: dict[str, bytes]
    hashes: dict[str, str]


def _status(message: str) -> None:
    sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


def _fail(message: str) -> Never:
    raise RotationError(message)


def _disable_core_dumps() -> None:
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (OSError, ValueError):
        _fail("could not disable core dumps; refusing to handle credentials")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _read_private_file(path: Path, *, max_bytes: int = MAX_SECRET_FILE_BYTES) -> bytes:
    # O_NONBLOCK prevents a malicious/surprising FIFO from hanging before the
    # post-open fstat can reject it. It has no effect on regular-file reads.
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RotationError(f"cannot safely open protected file: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            _fail(f"protected path is not a regular file: {path}")
        if info.st_uid != os.geteuid():
            _fail(f"protected file is not owned by the current operator: {path}")
        if stat.S_IMODE(info.st_mode) & 0o077:
            _fail(f"protected file must not grant group/other permissions: {path}")
        if info.st_size > max_bytes:
            _fail(f"protected file is unexpectedly large: {path}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            _fail(f"protected file is unexpectedly large: {path}")
        return data
    finally:
        os.close(fd)


def _write_new_private_file(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RotationError(f"cannot create protected file: {path}") from exc
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            _fail("protected file write did not complete")
        view = view[written:]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_env(data: bytes) -> tuple[list[str], dict[str, _Assignment]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RotationError("protected environment file is not valid UTF-8") from exc

    lines = text.splitlines(keepends=True)
    assignments: dict[str, _Assignment] = {}
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        match = _ASSIGNMENT_RE.match(body)
        if not match:
            continue
        key = match.group("key")
        if key not in ENV_KEYS:
            continue
        if key in assignments:
            _fail(f"environment file contains duplicate {key} assignments")
        raw_value = match.group("value")
        quote = ""
        value = raw_value
        if raw_value.startswith(("'", '"')):
            quote = raw_value[0]
            if len(raw_value) < 2 or not raw_value.endswith(quote):
                _fail(f"{key} has an unterminated quoted value")
            value = raw_value[1:-1]
        elif value != value.strip() or re.search(r"\s+#", value):
            _fail(f"{key} must not contain surrounding whitespace or an inline comment")
        if not value:
            _fail(f"{key} is empty")
        assignments[key] = _Assignment(
            index=index,
            prefix=match.group("prefix"),
            value=value,
            quote=quote,
            newline=newline,
        )

    missing = [key for key in ENV_KEYS if key not in assignments]
    if missing:
        _fail("environment file is missing required database DSN assignments")
    return lines, assignments


def _parse_dsn(key: str, value: str) -> _Dsn:
    try:
        parts = urlsplit(value)
        hostname = parts.hostname or ""
        port = parts.port or DB_PORT
    except ValueError as exc:
        raise RotationError(f"{key} is not a valid PostgreSQL DSN") from exc
    if parts.scheme not in {
        "postgres",
        "postgresql",
        "postgresql+asyncpg",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    }:
        _fail(f"{key} is not a supported PostgreSQL DSN")
    userinfo, separator, _hostinfo = parts.netloc.rpartition("@")
    username_raw, password_separator, password_raw = userinfo.partition(":")
    if not separator or not password_separator:
        _fail(f"{key} must include an explicit username and password")
    username = unquote(username_raw)
    password = unquote(password_raw)
    database = unquote(parts.path.removeprefix("/"))
    if not username or not password:
        _fail(f"{key} must include a non-empty username and password")
    return _Dsn(
        key=key,
        scheme=parts.scheme,
        username=username,
        password=password,
        hostname=hostname,
        port=port,
        database=database,
    )


def _validate_env(data: bytes) -> dict[str, _Dsn]:
    _lines, assignments = _decode_env(data)
    dsns = {key: _parse_dsn(key, assignments[key].value) for key in ENV_KEYS}
    if dsns["DATABASE_URL"].username != RUNTIME_ROLE:
        _fail("DATABASE_URL is not bound to the expected runtime role")
    if dsns["AUDIT_DATABASE_URL"].username != RUNTIME_ROLE:
        _fail("AUDIT_DATABASE_URL is not bound to the expected shared runtime role")
    if dsns["MIGRATION_DATABASE_URL"].username != MIGRATION_ROLE:
        _fail("MIGRATION_DATABASE_URL is not bound to the expected migration role")
    for dsn in dsns.values():
        if (dsn.hostname, dsn.port, dsn.database) != (DB_HOST, DB_PORT, DB_NAME):
            _fail(f"{dsn.key} does not point at the expected production database")
    if dsns["DATABASE_URL"].password != dsns["AUDIT_DATABASE_URL"].password:
        _fail("runtime and audit DSNs do not share the expected credential")
    if dsns["DATABASE_URL"].password == dsns["MIGRATION_DATABASE_URL"].password:
        _fail("runtime and migration credentials are not separated")
    return dsns


def _replace_dsn_password(value: str, password: str) -> str:
    if not _SAFE_PASSWORD_RE.fullmatch(password):
        _fail("generated password failed the internal format check")
    parts = urlsplit(value)
    userinfo, separator, hostinfo = parts.netloc.rpartition("@")
    username_raw, password_separator, _old_password = userinfo.partition(":")
    if not separator or not password_separator:
        _fail("database DSN cannot be safely rewritten")
    netloc = f"{username_raw}:{password}@{hostinfo}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _rewrite_env(data: bytes, replacements: dict[str, str]) -> bytes:
    lines, assignments = _decode_env(data)
    for key, password in replacements.items():
        assignment = assignments[key]
        value = _replace_dsn_password(assignment.value, password)
        lines[assignment.index] = (
            f"{assignment.prefix}{assignment.quote}{value}{assignment.quote}"
            f"{assignment.newline}"
        )
    return "".join(lines).encode()


def _new_password(*, forbidden: set[str]) -> str:
    while True:
        candidate = secrets.token_hex(32)
        if candidate not in forbidden:
            return candidate


def _validate_staged_relationships(
    previous: dict[str, _Dsn],
    runtime: dict[str, _Dsn],
    final: dict[str, _Dsn],
) -> None:
    previous_runtime = previous["DATABASE_URL"].password
    previous_migration = previous["MIGRATION_DATABASE_URL"].password
    runtime_password = runtime["DATABASE_URL"].password
    final_migration = final["MIGRATION_DATABASE_URL"].password
    if runtime_password == previous_runtime:
        _fail("staged runtime credential did not change")
    if runtime["MIGRATION_DATABASE_URL"].password != previous_migration:
        _fail("runtime phase changed the migration credential")
    if final["DATABASE_URL"].password != runtime_password:
        _fail("final phase drifted the runtime credential")
    if final_migration == previous_migration:
        _fail("staged migration credential did not change")
    if final_migration == runtime_password:
        _fail("staged runtime and migration credentials are not separated")


def stage(source_env: Path, state_dir: Path) -> None:
    source_env = source_env.expanduser()
    if not source_env.is_absolute():
        source_env = Path.cwd() / source_env
    try:
        if stat.S_ISLNK(source_env.lstat().st_mode):
            _fail("source environment must not be a symlink")
    except FileNotFoundError as exc:
        raise RotationError("source environment file does not exist") from exc
    source_env = source_env.resolve(strict=True)
    state_dir = state_dir.expanduser()
    if not state_dir.is_absolute():
        _fail("state directory must be an explicit absolute path")
    state_dir = state_dir.resolve(strict=False)
    if state_dir.exists():
        _fail("state directory already exists; use a new empty target")
    if _is_within(state_dir, source_env.parent):
        _fail("state directory must be outside the Git checkout")
    if not state_dir.parent.is_dir():
        _fail("state directory parent does not exist")

    previous_data = _read_private_file(source_env)
    previous_dsns = _validate_env(previous_data)
    forbidden = {dsn.password for dsn in previous_dsns.values()}
    runtime_password = _new_password(forbidden=forbidden)
    forbidden.add(runtime_password)
    migration_password = _new_password(forbidden=forbidden)

    runtime_data = _rewrite_env(
        previous_data,
        {
            "DATABASE_URL": runtime_password,
            "AUDIT_DATABASE_URL": runtime_password,
        },
    )
    final_data = _rewrite_env(runtime_data, {"MIGRATION_DATABASE_URL": migration_password})
    runtime_dsns = _validate_env(runtime_data)
    final_dsns = _validate_env(final_data)
    _validate_staged_relationships(previous_dsns, runtime_dsns, final_dsns)

    hashes = {
        "previous.env": _sha256(previous_data),
        "runtime.env": _sha256(runtime_data),
        "final.env": _sha256(final_data),
    }
    manifest = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source_env": str(source_env),
        "database": {
            "container": DB_CONTAINER_DEFAULT,
            "host": DB_HOST,
            "port": DB_PORT,
            "name": DB_NAME,
        },
        "roles": {
            "runtime": RUNTIME_ROLE,
            "migration": MIGRATION_ROLE,
            "audit_policy": AUDIT_POLICY_ROLE,
        },
        "sha256": hashes,
    }
    manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()

    temporary = state_dir.parent / f".{state_dir.name}.staging-{uuid.uuid4().hex}"
    try:
        os.mkdir(temporary, 0o700)
        _write_new_private_file(temporary / "previous.env", previous_data)
        _write_new_private_file(temporary / "runtime.env", runtime_data)
        _write_new_private_file(temporary / "final.env", final_data)
        _write_new_private_file(temporary / "manifest.json", manifest_data)
        os.replace(temporary, state_dir)
        parent_fd = os.open(state_dir.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except Exception:
        for filename in STATE_FILES:
            try:
                (temporary / filename).unlink()
            except FileNotFoundError:
                pass
        try:
            temporary.rmdir()
        except FileNotFoundError:
            pass
        raise

    _status(f"staged protected rotation state at {state_dir}")
    _status("no credential values were printed")


def _load_state(state_dir: Path) -> _State:
    state_dir = state_dir.expanduser()
    if not state_dir.is_absolute():
        state_dir = Path.cwd() / state_dir
    try:
        if stat.S_ISLNK(state_dir.lstat().st_mode):
            _fail("rotation state directory must not be a symlink")
    except FileNotFoundError as exc:
        raise RotationError("rotation state directory does not exist") from exc
    state_dir = state_dir.resolve(strict=True)
    info = state_dir.stat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        _fail("rotation state directory must be private (mode 0700 or stricter)")
    if info.st_uid != os.geteuid():
        _fail("rotation state directory is not owned by the current operator")
    manifest_data = _read_private_file(state_dir / "manifest.json")
    try:
        manifest = json.loads(manifest_data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RotationError("rotation manifest is invalid") from exc
    if manifest.get("version") != 1:
        _fail("unsupported rotation manifest version")
    try:
        source_env = Path(manifest["source_env"])
        expected_hashes = manifest["sha256"]
    except (KeyError, TypeError) as exc:
        raise RotationError("rotation manifest is incomplete") from exc
    if not source_env.is_absolute():
        _fail("rotation manifest contains a non-absolute source path")

    files: dict[str, bytes] = {}
    hashes: dict[str, str] = {}
    for filename in ("previous.env", "runtime.env", "final.env"):
        data = _read_private_file(state_dir / filename)
        digest = _sha256(data)
        expected = expected_hashes.get(filename) if isinstance(expected_hashes, dict) else None
        if not isinstance(expected, str) or not hmac.compare_digest(digest, expected):
            _fail(f"rotation state integrity check failed for {filename}")
        files[filename] = data
        hashes[filename] = digest

    previous = _validate_env(files["previous.env"])
    runtime = _validate_env(files["runtime.env"])
    final = _validate_env(files["final.env"])
    _validate_staged_relationships(previous, runtime, final)
    return _State(
        directory=state_dir,
        source_env=source_env,
        files=files,
        hashes=hashes,
    )


def _atomic_replace_private(path: Path, data: bytes) -> None:
    # Read first to enforce regular-file/no-symlink/private-mode constraints.
    _read_private_file(path)
    temp_fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.rotation-", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(temp_fd, 0o600)
        _write_all(temp_fd, data)
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = -1
        os.replace(temp_path, path)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


_ACTIVATIONS: Final = {
    "runtime": ("runtime.env", {"previous.env"}),
    "final": ("final.env", {"runtime.env"}),
    "rollback-migration": ("runtime.env", {"final.env"}),
    "rollback-all": ("previous.env", {"runtime.env", "final.env"}),
}


def activate(state_dir: Path, action: str) -> None:
    state = _load_state(state_dir)
    target_name, allowed_current = _ACTIVATIONS[action]
    current_data = _read_private_file(state.source_env)
    current_hash = _sha256(current_data)
    target_hash = state.hashes[target_name]
    if hmac.compare_digest(current_hash, target_hash):
        _status(f"configuration already matches activation action {action}")
        return
    if not any(
        hmac.compare_digest(current_hash, state.hashes[name]) for name in allowed_current
    ):
        _fail("live environment drifted from the staged sequence; refusing to overwrite it")
    _confirm(f"activate-{action}")
    _atomic_replace_private(state.source_env, state.files[target_name])
    if not hmac.compare_digest(_sha256(_read_private_file(state.source_env)), target_hash):
        _fail("atomic environment activation could not be verified")
    _status(f"activated {action} database configuration without printing credentials")


def _docker_binary() -> str:
    docker = shutil.which("docker")
    if not docker:
        _fail("docker is not available on PATH")
    return docker


def _validate_container(container: str) -> None:
    if not _CONTAINER_RE.fullmatch(container):
        _fail("database container name contains unsupported characters")


def _run_command(command: list[str], payload: bytes, *, timeout: int = 60) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable + validated arguments.
            command,
            input=payload,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RotationError("database control command failed before completion") from exc
    if result.returncode != 0:
        # Child output is intentionally withheld: psql diagnostics can include
        # connection strings under unexpected client/server configurations.
        _fail("database control command failed; credentials were not printed")
    return result.stdout


def _admin_query(container: str, sql: str) -> str:
    _validate_container(container)
    command = [
        _docker_binary(),
        "exec",
        "-i",
        container,
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-qAt",
        "-U",
        MIGRATION_ROLE,
        "-d",
        DB_NAME,
    ]
    output = _run_command(command, sql.encode())
    try:
        return output.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RotationError("database returned an invalid diagnostic result") from exc


def _pgpass_escape(value: str) -> str:
    if "\n" in value or "\r" in value:
        _fail("credential contains a forbidden newline")
    return value.replace("\\", "\\\\").replace(":", "\\:")


def _container_tcp_host(container: str) -> str:
    _validate_container(container)
    command = [
        _docker_binary(),
        "inspect",
        "--format",
        (
            "{{range $name, $settings := .NetworkSettings.Networks}}"
            "{{println $name $settings.IPAddress}}{{end}}"
        ),
        container,
    ]
    output = _run_command(command, b"")
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RotationError("docker returned invalid network metadata") from exc
    networks: dict[str, str] = {}
    for line in lines:
        fields = line.split()
        if len(fields) != 2:
            continue
        name, address = fields
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.version == 4:
            networks[name] = address
    if PRODUCTION_DB_NETWORK in networks:
        return networks[PRODUCTION_DB_NETWORK]
    if len(networks) == 1:
        return next(iter(networks.values()))
    _fail("could not select a unique password-authenticated database network")


_TCP_QUERY_SCRIPT: Final = r"""
credential_file="$(mktemp)"
query_file="$(mktemp)"
cleanup() { rm -f "$credential_file" "$query_file"; }
trap cleanup EXIT HUP INT TERM
chmod 600 "$credential_file" "$query_file"
IFS= read -r pgpass_line
printf '%s\n' "$pgpass_line" >"$credential_file"
cat >"$query_file"
PGPASSFILE="$credential_file" psql -X -v ON_ERROR_STOP=1 -qAt \
  -h "$3" -p 5432 -U "$1" -d "$2" -f "$query_file"
"""


def _tcp_query(container: str, username: str, password: str, sql: str) -> tuple[bool, str]:
    _validate_container(container)
    tcp_host = _container_tcp_host(container)
    pgpass = ":".join(
        (
            _pgpass_escape(tcp_host),
            str(DB_PORT),
            _pgpass_escape(DB_NAME),
            _pgpass_escape(username),
            _pgpass_escape(password),
        )
    )
    command = [
        _docker_binary(),
        "exec",
        "-i",
        container,
        "sh",
        "-ceu",
        _TCP_QUERY_SCRIPT,
        "--",
        username,
        DB_NAME,
        tcp_host,
    ]
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable + validated arguments.
            command,
            input=(pgpass + "\n" + sql).encode(),
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RotationError("database authentication probe failed before completion") from exc
    try:
        output = result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RotationError("database returned an invalid authentication result") from exc
    return result.returncode == 0, output


def _wrong_password(password: str) -> str:
    replacement = "0" if not password.startswith("0") else "1"
    return replacement + password[1:]


def _assert_password_auth(
    container: str,
    username: str,
    password: str,
    sql: str,
    expected: tuple[str, ...],
) -> None:
    wrong_ok, _wrong_output = _tcp_query(
        container, username, _wrong_password(password), "SELECT 'UNEXPECTED';\n"
    )
    if wrong_ok:
        _fail("TCP password authentication accepted a known-wrong credential")
    correct_ok, output = _tcp_query(container, username, password, sql)
    if not correct_ok:
        _fail(f"authenticated database query failed for role {username}")
    output_lines = output.splitlines()
    missing = [item for item in expected if item not in output_lines]
    if missing:
        _fail(f"database verification checks failed for role {username}: {', '.join(missing)}")


def _verify_local_role_contract(container: str) -> None:
    result = _admin_query(
        container,
        f"""
SELECT CASE WHEN
  (SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
     FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}')
  AND pg_has_role('{RUNTIME_ROLE}', '{APP_POLICY_ROLE}', 'member')
  AND (SELECT NOT rolcanlogin FROM pg_roles WHERE rolname = '{AUDIT_POLICY_ROLE}')
  AND (SELECT rolcanlogin AND (rolsuper OR rolbypassrls)
         FROM pg_roles WHERE rolname = '{MIGRATION_ROLE}')
THEN 'LOCAL_ROLE_OK' ELSE 'LOCAL_ROLE_FAIL' END;
SHOW password_encryption;
""",
    )
    lines = result.splitlines()
    if "LOCAL_ROLE_OK" not in lines:
        _fail("database role separation/NOBYPASSRLS preflight failed")
    if "scram-sha-256" not in lines:
        _fail("PostgreSQL password_encryption is not scram-sha-256")


def _tenant_probe_ids(container: str) -> tuple[str, str]:
    output = _admin_query(
        container,
        """
SELECT canonical_owner_tenant_id::text
FROM persons
GROUP BY canonical_owner_tenant_id
ORDER BY canonical_owner_tenant_id
LIMIT 2;
""",
    )
    lines = [line for line in output.splitlines() if line]
    if len(lines) != 2:
        _fail("RLS verification requires persons owned by two tenants; refusing to skip")
    try:
        ids = (str(uuid.UUID(lines[0])), str(uuid.UUID(lines[1])))
    except ValueError as exc:
        raise RotationError("database returned invalid tenant ids for the RLS probe") from exc
    return ids


def _verify_runtime(container: str, password: str) -> None:
    first_tenant, second_tenant = _tenant_probe_ids(container)
    # The only interpolated values are fixed role names and UUIDs that were
    # parsed with uuid.UUID immediately above.
    sql = f"""
SELECT CASE WHEN current_user = '{RUNTIME_ROLE}'
  AND (SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
         FROM pg_roles WHERE rolname = current_user)
  AND pg_has_role(current_user, '{APP_POLICY_ROLE}', 'member')
THEN 'RUNTIME_ROLE_OK' ELSE 'RUNTIME_ROLE_FAIL' END;
SELECT CASE WHEN has_table_privilege(current_user, 'public.action_event', 'INSERT')
  AND NOT has_table_privilege(current_user, 'public.action_event', 'DELETE')
THEN 'AUDIT_PATH_OK' ELSE 'AUDIT_PATH_FAIL' END;
SELECT CASE WHEN relrowsecurity AND relforcerowsecurity
  THEN 'FORCE_RLS_OK' ELSE 'FORCE_RLS_FAIL' END
FROM pg_class WHERE oid = 'public.persons'::regclass;
WITH bound AS MATERIALIZED (
  SELECT set_config('app.tenant_id', '{first_tenant}', true)
)
SELECT CASE WHEN (
  SELECT count(*) FROM persons
   WHERE canonical_owner_tenant_id = '{first_tenant}'::uuid
) > 0 AND (
  SELECT count(*) FROM persons
   WHERE canonical_owner_tenant_id = '{second_tenant}'::uuid
) = 0 THEN 'CROSS_TENANT_RLS_OK' ELSE 'CROSS_TENANT_RLS_FAIL' END
FROM bound;
"""
    _assert_password_auth(
        container,
        RUNTIME_ROLE,
        password,
        sql,
        (
            "RUNTIME_ROLE_OK",
            "AUDIT_PATH_OK",
            "FORCE_RLS_OK",
            "CROSS_TENANT_RLS_OK",
        ),
    )


def _verify_migration(container: str, password: str) -> None:
    sql = f"""
SELECT CASE WHEN current_user = '{MIGRATION_ROLE}'
  AND (SELECT rolcanlogin AND (rolsuper OR rolbypassrls)
         FROM pg_roles WHERE rolname = current_user)
  AND current_user <> '{RUNTIME_ROLE}'
THEN 'MIGRATION_ROLE_OK' ELSE 'MIGRATION_ROLE_FAIL' END;
"""
    _assert_password_auth(container, MIGRATION_ROLE, password, sql, ("MIGRATION_ROLE_OK",))


def _verify_phase(state: _State, phase: str, container: str, *, require_live: bool) -> None:
    if require_live:
        live_hash = _sha256(_read_private_file(state.source_env))
        if not hmac.compare_digest(live_hash, state.hashes[f"{phase}.env"]):
            _fail(f"live environment does not match the staged {phase} phase")
    data = state.files[f"{phase}.env"]
    dsns = _validate_env(data)
    _verify_local_role_contract(container)
    _verify_runtime(container, dsns["DATABASE_URL"].password)
    _verify_migration(container, dsns["MIGRATION_DATABASE_URL"].password)
    _status(f"verified {phase} credentials, role separation, and cross-tenant RLS")


def verify(state_dir: Path, phase: str, container: str) -> None:
    state = _load_state(state_dir)
    _verify_phase(state, phase, container, require_live=True)


_ROLE_ACTIONS: Final = {
    "runtime-new": (RUNTIME_ROLE, "runtime.env", "DATABASE_URL"),
    "migration-new": (MIGRATION_ROLE, "final.env", "MIGRATION_DATABASE_URL"),
    "migration-rollback": (MIGRATION_ROLE, "runtime.env", "MIGRATION_DATABASE_URL"),
    "runtime-rollback": (RUNTIME_ROLE, "previous.env", "DATABASE_URL"),
}


def _confirm(expected: str) -> None:
    if not sys.stdin.isatty():
        _fail("typed confirmation requires an interactive terminal")
    entered = input(f"Type '{expected}' to continue: ")
    if not hmac.compare_digest(entered, expected):
        _fail("typed confirmation did not match; no change was made")


def _set_password_with_psql(container: str, role: str, password: str) -> None:
    _validate_container(container)
    if role not in {RUNTIME_ROLE, MIGRATION_ROLE}:
        _fail("refusing to change an unexpected database role")
    if "\n" in password or "\r" in password:
        _fail("credential contains a forbidden newline")
    payload = f"\\set ON_ERROR_STOP on\n\\password {role}\n{password}\n{password}\n\\q\n"
    command = [
        _docker_binary(),
        "exec",
        "-i",
        container,
        "psql",
        "-X",
        "-U",
        MIGRATION_ROLE,
        "-d",
        DB_NAME,
    ]
    _run_command(command, payload.encode())


def role_password(state_dir: Path, action: str, container: str) -> None:
    state = _load_state(state_dir)
    role, filename, key = _ROLE_ACTIONS[action]
    dsns = _validate_env(state.files[filename])
    password = dsns[key].password
    _verify_local_role_contract(container)
    _confirm(action)
    _set_password_with_psql(container, role, password)
    phase = filename.removesuffix(".env")
    _verify_phase(state, phase, container, require_live=False)
    _status(f"completed and verified database role action {action}")


def preflight(state_dir: Path, container: str) -> None:
    state = _load_state(state_dir)
    _verify_phase(state, "previous", container, require_live=True)
    _status("preflight passed; no database or service state was changed")


def _assert_rejected(container: str, username: str, password: str) -> None:
    accepted, _output = _tcp_query(container, username, password, "SELECT 'ACCEPTED';\n")
    if accepted:
        _fail(f"retired credential still authenticates for role {username}")


def _verify_retired_state(state: _State, container: str) -> None:
    previous = _validate_env(state.files["previous.env"])
    _assert_rejected(container, RUNTIME_ROLE, previous["DATABASE_URL"].password)
    _assert_rejected(container, MIGRATION_ROLE, previous["MIGRATION_DATABASE_URL"].password)


def verify_retired(state_dir: Path, container: str) -> None:
    state = _load_state(state_dir)
    _verify_phase(state, "final", container, require_live=True)
    _verify_retired_state(state, container)
    _status("verified both retired database credentials are rejected")


def finalize(state_dir: Path, container: str) -> None:
    state = _load_state(state_dir)
    _verify_phase(state, "final", container, require_live=True)
    _verify_retired_state(state, container)
    _confirm("finalize-and-remove-rollback")
    for filename in STATE_FILES:
        (state.directory / filename).unlink()
    state.directory.rmdir()
    parent_fd = os.open(state.directory.parent, os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    _status(f"removed protected rotation state {state.directory}")
    _status("the staged rollback copy is no longer available")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely stage and execute the Contact-Ops PostgreSQL credential rotation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage_parser = subparsers.add_parser("stage", help="create protected phased configs")
    stage_parser.add_argument("--source-env", required=True, type=Path)
    stage_parser.add_argument("--state-dir", required=True, type=Path)

    preflight_parser = subparsers.add_parser("preflight", help="read-only live checks")
    preflight_parser.add_argument("--state-dir", required=True, type=Path)
    preflight_parser.add_argument("--db-container", default=DB_CONTAINER_DEFAULT)

    role_parser = subparsers.add_parser("role-password", help="change one role password")
    role_parser.add_argument("action", choices=tuple(_ROLE_ACTIONS))
    role_parser.add_argument("--state-dir", required=True, type=Path)
    role_parser.add_argument("--db-container", default=DB_CONTAINER_DEFAULT)

    activate_parser = subparsers.add_parser("activate", help="atomically activate a config")
    activate_parser.add_argument("action", choices=tuple(_ACTIVATIONS))
    activate_parser.add_argument("--state-dir", required=True, type=Path)

    verify_parser = subparsers.add_parser("verify", help="verify a staged credential phase")
    verify_parser.add_argument("phase", choices=("previous", "runtime", "final"))
    verify_parser.add_argument("--state-dir", required=True, type=Path)
    verify_parser.add_argument("--db-container", default=DB_CONTAINER_DEFAULT)

    retired_parser = subparsers.add_parser(
        "verify-retired", help="prove the two previous credentials are rejected"
    )
    retired_parser.add_argument("--state-dir", required=True, type=Path)
    retired_parser.add_argument("--db-container", default=DB_CONTAINER_DEFAULT)

    finalize_parser = subparsers.add_parser(
        "finalize", help="verify final state and remove staged rollback secrets"
    )
    finalize_parser.add_argument("--state-dir", required=True, type=Path)
    finalize_parser.add_argument("--db-container", default=DB_CONTAINER_DEFAULT)
    return parser


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    _disable_core_dumps()
    args = _parser().parse_args(argv)
    if args.command == "stage":
        stage(args.source_env, args.state_dir)
    elif args.command == "preflight":
        preflight(args.state_dir, args.db_container)
    elif args.command == "role-password":
        role_password(args.state_dir, args.action, args.db_container)
    elif args.command == "activate":
        activate(args.state_dir, args.action)
    elif args.command == "verify":
        verify(args.state_dir, args.phase, args.db_container)
    elif args.command == "verify-retired":
        verify_retired(args.state_dir, args.db_container)
    elif args.command == "finalize":
        finalize(args.state_dir, args.db_container)
    else:  # pragma: no cover - argparse requires a known command.
        _fail("unknown command")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RotationError as exc:
        # RotationError messages are authored to contain paths/status only,
        # never DSN values or subprocess output.
        sys.stderr.write(f"ERROR: {exc}\n")
        raise SystemExit(2) from None
