#!/usr/bin/env python3
# ruff: noqa: S105, S108, S608 -- fixed role SQL; private container temp paths hold no literals.
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
import fcntl
import hashlib
import hmac
import ipaddress
import json
import math
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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
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
STATE_FILES: Final = (
    "previous.env",
    "runtime.env",
    "final.env",
    "manifest.json",
    "state.json",
    "rotation.lock",
)
MAX_SECRET_FILE_BYTES: Final = 1024 * 1024

PASSWORD_MUTATION_LOCK_DIR: Final = "/tmp/contact-ops-db-credential-rotation.lock"
TCP_PROBE_DIRECTORY_PREFIX: Final = "/tmp/contact-ops-db-auth-probe"
PASSWORD_CONTAINER_DEADLINE_SECONDS: Final = 45
TCP_CONTAINER_DEADLINE_SECONDS: Final = 45
CONTAINER_KILL_GRACE_SECONDS: Final = 5
PASSWORD_HOST_WATCHDOG_SECONDS: Final = 70
TCP_HOST_WATCHDOG_SECONDS: Final = 70
PG_CONNECT_TIMEOUT_SECONDS: Final = 10
PG_LOCK_TIMEOUT_MILLISECONDS: Final = 10_000
PG_STATEMENT_TIMEOUT_MILLISECONDS: Final = 30_000
RECOVERY_QUIESCENCE_SECONDS: Final = 60
CONTAINER_MUTATION_BUSY_EXIT_CODE: Final = 75

PHASE_STAGED: Final = "staged"
PHASE_RUNTIME_NEW_PENDING: Final = "runtime-new-pending"
PHASE_RUNTIME_ROLE_APPLIED: Final = "runtime-role-applied"
PHASE_RUNTIME_ACTIVE: Final = "runtime-active"
PHASE_MIGRATION_NEW_PENDING: Final = "migration-new-pending"
PHASE_MIGRATION_ROLE_APPLIED: Final = "migration-role-applied"
PHASE_FINAL_ACTIVE: Final = "final-active"
PHASE_MIGRATION_ROLLBACK_PENDING: Final = "migration-rollback-pending"
PHASE_RUNTIME_ROLLBACK_PENDING: Final = "runtime-rollback-pending"

_VALID_PHASES: Final = frozenset(
    {
        PHASE_STAGED,
        PHASE_RUNTIME_NEW_PENDING,
        PHASE_RUNTIME_ROLE_APPLIED,
        PHASE_RUNTIME_ACTIVE,
        PHASE_MIGRATION_NEW_PENDING,
        PHASE_MIGRATION_ROLE_APPLIED,
        PHASE_FINAL_ACTIVE,
        PHASE_MIGRATION_ROLLBACK_PENDING,
        PHASE_RUNTIME_ROLLBACK_PENDING,
    }
)

_PENDING_ORIGINS: Final = {
    PHASE_RUNTIME_NEW_PENDING: frozenset({PHASE_STAGED}),
    PHASE_MIGRATION_NEW_PENDING: frozenset({PHASE_RUNTIME_ACTIVE}),
    PHASE_MIGRATION_ROLLBACK_PENDING: frozenset({PHASE_MIGRATION_ROLE_APPLIED, PHASE_FINAL_ACTIVE}),
    PHASE_RUNTIME_ROLLBACK_PENDING: frozenset({PHASE_RUNTIME_ROLE_APPLIED, PHASE_RUNTIME_ACTIVE}),
}

_ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)(?P<value>.*)$"
)
_SAFE_PASSWORD_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class RotationError(RuntimeError):
    """Expected fail-closed error whose text is safe to show an operator."""


class _IndeterminateMutationError(RotationError):
    """A prior container-side mutation may still be running."""


class _ProbeStatus(Enum):
    SUCCESS = "success"
    AUTH_REJECTED = "auth-rejected"
    FAILURE = "failure"


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
    phase: str
    origin_phase: str | None
    updated_at: datetime


@dataclass(frozen=True)
class _ProbeResult:
    status: _ProbeStatus
    output: str


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
            f"{assignment.prefix}{assignment.quote}{value}{assignment.quote}{assignment.newline}"
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


def _phase_document(phase: str, origin_phase: str | None = None) -> bytes:
    if phase not in _VALID_PHASES:
        _fail("refusing to persist an unknown rotation phase")
    if phase in _PENDING_ORIGINS:
        if origin_phase not in _PENDING_ORIGINS[phase]:
            _fail("refusing to persist an invalid transition origin")
    elif origin_phase is not None:
        _fail("refusing to persist an origin on a stable rotation phase")
    document = {
        "version": 1,
        "phase": phase,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if origin_phase is not None:
        document["origin_phase"] = origin_phase
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


def _parse_phase(data: bytes) -> tuple[str, str | None, datetime]:
    try:
        document = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RotationError("rotation phase journal is invalid") from exc
    if document.get("version") != 1 or document.get("phase") not in _VALID_PHASES:
        _fail("rotation phase journal is incomplete or unsupported")
    try:
        updated_at = datetime.fromisoformat(str(document["updated_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RotationError("rotation phase journal has an invalid timestamp") from exc
    if updated_at.tzinfo is None:
        _fail("rotation phase journal timestamp must include a timezone")
    updated_at = updated_at.astimezone(UTC)
    phase = str(document["phase"])
    origin = document.get("origin_phase")
    if phase in _PENDING_ORIGINS:
        if origin not in _PENDING_ORIGINS[phase]:
            _fail("pending rotation phase has an invalid stable origin")
        return phase, str(origin), updated_at
    if origin is not None:
        _fail("stable rotation phase must not retain an origin")
    return phase, None, updated_at


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
        "version": 2,
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
        _write_new_private_file(temporary / "state.json", _phase_document(PHASE_STAGED))
        _write_new_private_file(temporary / "rotation.lock", b"")
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
    if manifest.get("version") != 2:
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
    phase, origin_phase, updated_at = _parse_phase(_read_private_file(state_dir / "state.json"))
    return _State(
        directory=state_dir,
        source_env=source_env,
        files=files,
        hashes=hashes,
        phase=phase,
        origin_phase=origin_phase,
        updated_at=updated_at,
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


@contextmanager
def _state_lock(state_dir: Path) -> Iterator[Path]:
    candidate = state_dir.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        if stat.S_ISLNK(candidate.lstat().st_mode):
            _fail("rotation state directory must not be a symlink")
    except FileNotFoundError as exc:
        raise RotationError("rotation state directory does not exist") from exc
    resolved = candidate.resolve(strict=True)
    lock_path = resolved / "rotation.lock"
    flags = os.O_RDWR | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags)
    except OSError as exc:
        raise RotationError("cannot safely open the rotation lock") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            _fail("rotation lock is not a current-operator-owned regular file")
        if stat.S_IMODE(info.st_mode) & 0o077:
            _fail("rotation lock must not grant group/other permissions")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield resolved
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _set_phase(state: _State, phase: str, origin_phase: str | None = None) -> None:
    phase_path = state.directory / "state.json"
    _atomic_replace_private(phase_path, _phase_document(phase, origin_phase))
    actual_phase, actual_origin, _updated_at = _parse_phase(_read_private_file(phase_path))
    if (actual_phase, actual_origin) != (phase, origin_phase):
        _fail("rotation phase journal update could not be verified")


def _live_phase(state: _State) -> str:
    live_hash = _sha256(_read_private_file(state.source_env))
    for phase, filename in (
        ("previous", "previous.env"),
        ("runtime", "runtime.env"),
        ("final", "final.env"),
    ):
        if hmac.compare_digest(live_hash, state.hashes[filename]):
            return phase
    _fail("live environment drifted from every staged phase")


def _require_machine_phase(state: _State, action: str, allowed: set[str]) -> None:
    if state.phase not in allowed:
        expected = ", ".join(sorted(allowed))
        _fail(f"action {action} is not allowed from phase {state.phase}; expected {expected}")


def _require_recovery_quiescence(state: _State) -> None:
    elapsed = (datetime.now(UTC) - state.updated_at).total_seconds()
    remaining = RECOVERY_QUIESCENCE_SECONDS - elapsed
    if remaining > 0:
        _fail(
            "pending transition is still inside the container quiescence window; "
            f"wait at least {math.ceil(remaining)} more seconds before recover"
        )


_ACTIVATIONS: Final = {
    "runtime": (
        "runtime.env",
        "previous",
        PHASE_RUNTIME_ROLE_APPLIED,
        PHASE_RUNTIME_ACTIVE,
    ),
    "final": (
        "final.env",
        "runtime",
        PHASE_MIGRATION_ROLE_APPLIED,
        PHASE_FINAL_ACTIVE,
    ),
}


def _replace_live_config(state: _State, filename: str) -> None:
    _atomic_replace_private(state.source_env, state.files[filename])
    if not hmac.compare_digest(
        _sha256(_read_private_file(state.source_env)), state.hashes[filename]
    ):
        _fail("atomic environment activation could not be verified")


def _compensate_activation(state: _State, action: str, container: str) -> None:
    previous = _validate_env(state.files["previous.env"])
    runtime = _validate_env(state.files["runtime.env"])
    if action == "runtime":
        # Persist a recoverable transition before the compensating ALTER ROLE.
        _set_phase(state, PHASE_RUNTIME_NEW_PENDING, PHASE_STAGED)
        _replace_live_config(state, "previous.env")
        _set_password_while_pending(
            state,
            PHASE_RUNTIME_NEW_PENDING,
            PHASE_STAGED,
            container,
            RUNTIME_ROLE,
            previous["DATABASE_URL"].password,
        )
        _verify_phase(state, "previous", container, require_live=True)
        _set_phase(state, PHASE_STAGED)
        return
    _set_phase(state, PHASE_MIGRATION_NEW_PENDING, PHASE_RUNTIME_ACTIVE)
    _replace_live_config(state, "runtime.env")
    _set_password_while_pending(
        state,
        PHASE_MIGRATION_NEW_PENDING,
        PHASE_RUNTIME_ACTIVE,
        container,
        MIGRATION_ROLE,
        runtime["MIGRATION_DATABASE_URL"].password,
    )
    _verify_phase(state, "runtime", container, require_live=True)
    _set_phase(state, PHASE_RUNTIME_ACTIVE)


def activate(state_dir: Path, action: str, container: str) -> None:
    with _state_lock(state_dir) as locked_dir:
        state = _load_state(locked_dir)
        if action not in _ACTIVATIONS:
            _fail("refusing an unknown activation action")
        target_name, current_live_phase, required_phase, completed_phase = _ACTIVATIONS[action]
        _require_machine_phase(state, f"activate-{action}", {required_phase})
        live_phase = _live_phase(state)
        target_phase = target_name.removesuffix(".env")
        if live_phase == target_phase:
            _verify_phase(state, target_phase, container, require_live=True)
            _set_phase(state, completed_phase)
            _status(f"reconciled already-active {action} configuration")
            return
        if live_phase != current_live_phase:
            _fail(
                f"activation {action} requires live {current_live_phase} config; found {live_phase}"
            )

        # Verify the target role state before changing the live config. This is
        # the gate that prevents a config-first credential split.
        _verify_phase(state, target_phase, container, require_live=False)
        _confirm(f"activate-{action}")
        try:
            _replace_live_config(state, target_name)
            _verify_phase(state, target_phase, container, require_live=True)
            _set_phase(state, completed_phase)
        except Exception as exc:
            try:
                _compensate_activation(state, action, container)
            except Exception as compensation_exc:
                raise RotationError(
                    "activation failed and automatic compensation failed; "
                    f"writers must remain stopped, wait at least "
                    f"{RECOVERY_QUIESCENCE_SECONDS} seconds, then run recover"
                ) from compensation_exc
            restored_phase = PHASE_STAGED if action == "runtime" else PHASE_RUNTIME_ACTIVE
            raise RotationError(
                f"activation failed; database and config were restored to {restored_phase}"
            ) from exc
        _status(f"activated {action} database configuration without printing credentials")


def _docker_binary() -> str:
    docker = shutil.which("docker")
    if not docker:
        _fail("docker is not available on PATH")
    return docker


def _validate_container(container: str) -> None:
    if not _CONTAINER_RE.fullmatch(container):
        _fail("database container name contains unsupported characters")


def _validate_watchdog_order(
    container_deadline: float,
    kill_grace: float,
    host_watchdog: float,
) -> None:
    if min(container_deadline, kill_grace, host_watchdog) <= 0:
        _fail("database command deadlines must be positive")
    if host_watchdog <= container_deadline + kill_grace:
        _fail("host watchdog must exceed the container deadline and kill grace")


def _run_command(
    command: list[str],
    payload: bytes,
    *,
    timeout: float = 60,
    indeterminate_on_timeout: bool = False,
) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable + validated arguments.
            command,
            input=payload,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        if indeterminate_on_timeout:
            raise _IndeterminateMutationError(
                "host watchdog expired; container-side mutation status is indeterminate"
            ) from exc
        raise RotationError("database control command failed before completion") from exc
    except OSError as exc:
        raise RotationError("database control command failed before completion") from exc
    if result.returncode != 0:
        if indeterminate_on_timeout and result.returncode == CONTAINER_MUTATION_BUSY_EXIT_CODE:
            raise _IndeterminateMutationError("container-side password mutation lock is still held")
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
umask 077
command -v timeout >/dev/null
work_dir="$(mktemp -d "$8.XXXXXX")"
credential_file="$work_dir/pgpass"
query_file="$work_dir/query.sql"
cleanup() {
  rm -f "$credential_file" "$query_file"
  rmdir "$work_dir" 2>/dev/null || :
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
touch "$credential_file" "$query_file"
chmod 600 "$credential_file" "$query_file"
IFS= read -r pgpass_line
printf '%s\n' "$pgpass_line" >"$credential_file"
cat >"$query_file"
export PGPASSFILE="$credential_file"
export PGCONNECT_TIMEOUT="$6"
export PGOPTIONS="-c lock_timeout=$7 -c statement_timeout=$9"
timeout -s TERM -k "$5"s "$4"s \
  psql -X -v ON_ERROR_STOP=1 -qAt \
    -h "$3" -p 5432 -U "$1" -d "$2" -f "$query_file"
"""


def _tcp_query(container: str, username: str, password: str, sql: str) -> _ProbeResult:
    _validate_container(container)
    _validate_watchdog_order(
        TCP_CONTAINER_DEADLINE_SECONDS,
        CONTAINER_KILL_GRACE_SECONDS,
        TCP_HOST_WATCHDOG_SECONDS,
    )
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
        str(TCP_CONTAINER_DEADLINE_SECONDS),
        str(CONTAINER_KILL_GRACE_SECONDS),
        str(PG_CONNECT_TIMEOUT_SECONDS),
        str(PG_LOCK_TIMEOUT_MILLISECONDS),
        TCP_PROBE_DIRECTORY_PREFIX,
        str(PG_STATEMENT_TIMEOUT_MILLISECONDS),
    ]
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable + validated arguments.
            command,
            input=(pgpass + "\n" + sql).encode(),
            capture_output=True,
            check=False,
            timeout=TCP_HOST_WATCHDOG_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RotationError("database authentication probe failed before completion") from exc
    try:
        output = result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RotationError("database returned an invalid authentication result") from exc
    if result.returncode == 0:
        return _ProbeResult(_ProbeStatus.SUCCESS, output)
    stderr_lower = result.stderr.lower()
    if b"password authentication failed for user" in stderr_lower:
        return _ProbeResult(_ProbeStatus.AUTH_REJECTED, "")
    return _ProbeResult(_ProbeStatus.FAILURE, "")


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
    wrong_result = _tcp_query(
        container, username, _wrong_password(password), "SELECT 'UNEXPECTED';\n"
    )
    if wrong_result.status is _ProbeStatus.SUCCESS:
        _fail("TCP password authentication accepted a known-wrong credential")
    if wrong_result.status is not _ProbeStatus.AUTH_REJECTED:
        _fail("known-wrong credential probe failed without an authentication rejection")
    correct_result = _tcp_query(container, username, password, sql)
    if correct_result.status is not _ProbeStatus.SUCCESS:
        _fail(f"authenticated database query failed for role {username}")
    output_lines = correct_result.output.splitlines()
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
    required_machine = {
        "previous": PHASE_STAGED,
        "runtime": PHASE_RUNTIME_ACTIVE,
        "final": PHASE_FINAL_ACTIVE,
    }[phase]
    with _state_lock(state_dir) as locked_dir:
        state = _load_state(locked_dir)
        _require_machine_phase(state, f"verify-{phase}", {required_machine})
        _verify_phase(state, phase, container, require_live=True)


_ROLE_ACTIONS: Final = (
    "runtime-new",
    "migration-new",
    "migration-rollback",
    "runtime-rollback",
)

_ROLE_REQUIRED_PHASES: Final = {
    "runtime-new": frozenset({PHASE_STAGED}),
    "migration-new": frozenset({PHASE_RUNTIME_ACTIVE}),
    "migration-rollback": frozenset({PHASE_MIGRATION_ROLE_APPLIED, PHASE_FINAL_ACTIVE}),
    "runtime-rollback": frozenset({PHASE_RUNTIME_ROLE_APPLIED, PHASE_RUNTIME_ACTIVE}),
}


def _confirm(expected: str) -> None:
    if not sys.stdin.isatty():
        _fail("typed confirmation requires an interactive terminal")
    entered = input(f"Type '{expected}' to continue: ")
    if not hmac.compare_digest(entered, expected):
        _fail("typed confirmation did not match; no change was made")


_PASSWORD_CHANGE_SCRIPT: Final = r"""
umask 077
command -v timeout >/dev/null
lock_dir="$8"
if ! mkdir "$lock_dir" 2>/dev/null; then
  exit 75
fi
cleanup() {
  rm -f "$lock_dir/owner-pid"
  rmdir "$lock_dir" 2>/dev/null || :
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s\n' "$$" >"$lock_dir/owner-pid"
export PGCONNECT_TIMEOUT="$5"
export PGOPTIONS="-c lock_timeout=$6 -c statement_timeout=$7"
timeout -s TERM -k "$4"s "$3"s \
  psql -X -v ON_ERROR_STOP=1 -U "$1" -d "$2"
"""


def _set_password_with_psql(container: str, role: str, password: str) -> None:
    _validate_container(container)
    _validate_watchdog_order(
        PASSWORD_CONTAINER_DEADLINE_SECONDS,
        CONTAINER_KILL_GRACE_SECONDS,
        PASSWORD_HOST_WATCHDOG_SECONDS,
    )
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
        "sh",
        "-ceu",
        _PASSWORD_CHANGE_SCRIPT,
        "--",
        MIGRATION_ROLE,
        DB_NAME,
        str(PASSWORD_CONTAINER_DEADLINE_SECONDS),
        str(CONTAINER_KILL_GRACE_SECONDS),
        str(PG_CONNECT_TIMEOUT_SECONDS),
        str(PG_LOCK_TIMEOUT_MILLISECONDS),
        str(PG_STATEMENT_TIMEOUT_MILLISECONDS),
        PASSWORD_MUTATION_LOCK_DIR,
    ]
    _run_command(
        command,
        payload.encode(),
        timeout=PASSWORD_HOST_WATCHDOG_SECONDS,
        indeterminate_on_timeout=True,
    )


def _set_password_while_pending(
    state: _State,
    pending_phase: str,
    origin_phase: str,
    container: str,
    role: str,
    password: str,
) -> None:
    try:
        _set_password_with_psql(container, role, password)
    except _IndeterminateMutationError:
        # Refresh the recovery clock at an outer-watchdog or busy-lane boundary.
        # The container lock remains authoritative if Docker started the exec
        # unusually late or the helper died before it could refresh.
        try:
            _set_phase(state, pending_phase, origin_phase)
        except Exception as journal_exc:
            raise _IndeterminateMutationError(
                "indeterminate mutation and the pending journal could not be refreshed"
            ) from journal_exc
        raise


def _forward_role_password(state: _State, action: str, container: str) -> None:
    if action == "runtime-new":
        role = RUNTIME_ROLE
        key = "DATABASE_URL"
        source_config = "previous"
        target_config = "runtime"
        required_phase = PHASE_STAGED
        pending_phase = PHASE_RUNTIME_NEW_PENDING
        applied_phase = PHASE_RUNTIME_ROLE_APPLIED
    else:
        role = MIGRATION_ROLE
        key = "MIGRATION_DATABASE_URL"
        source_config = "runtime"
        target_config = "final"
        required_phase = PHASE_RUNTIME_ACTIVE
        pending_phase = PHASE_MIGRATION_NEW_PENDING
        applied_phase = PHASE_MIGRATION_ROLE_APPLIED

    _require_machine_phase(state, action, {required_phase})
    live_phase = _live_phase(state)
    if live_phase != source_config:
        _fail(f"role action {action} requires live {source_config} config; found {live_phase}")
    _verify_phase(state, source_config, container, require_live=True)

    source_dsns = _validate_env(state.files[f"{source_config}.env"])
    target_dsns = _validate_env(state.files[f"{target_config}.env"])
    old_password = source_dsns[key].password
    new_password = target_dsns[key].password
    _confirm(action)
    _set_phase(state, pending_phase, required_phase)
    try:
        _set_password_while_pending(
            state,
            pending_phase,
            required_phase,
            container,
            role,
            new_password,
        )
        _verify_phase(state, target_config, container, require_live=False)
        if _live_phase(state) != source_config:
            _fail("live config changed during the role-only transition")
        _set_phase(state, applied_phase)
    except _IndeterminateMutationError as exc:
        raise RotationError(
            f"role action {action} did not reach a determinate database completion; "
            f"it remains indeterminate in {pending_phase}. Keep writers stopped, "
            f"wait at least {RECOVERY_QUIESCENCE_SECONDS} seconds, then run recover"
        ) from exc
    except Exception as exc:
        try:
            _replace_live_config(state, f"{source_config}.env")
            _set_password_while_pending(
                state,
                pending_phase,
                required_phase,
                container,
                role,
                old_password,
            )
            _verify_phase(state, source_config, container, require_live=True)
            _set_phase(state, required_phase)
        except Exception as compensation_exc:
            raise RotationError(
                f"role action {action} failed and automatic compensation failed; "
                "writers must remain stopped and recover must be run"
            ) from compensation_exc
        raise RotationError(
            f"role action {action} failed; database and config were restored"
        ) from exc
    _status(f"completed and verified database role action {action}")


def _rollback_role_password(state: _State, action: str, container: str) -> None:
    if action == "migration-rollback":
        role = MIGRATION_ROLE
        key = "MIGRATION_DATABASE_URL"
        source_config = "final"
        target_config = "runtime"
        allowed_live = {
            PHASE_MIGRATION_ROLE_APPLIED: "runtime",
            PHASE_FINAL_ACTIVE: "final",
        }
        pending_phase = PHASE_MIGRATION_ROLLBACK_PENDING
        completed_phase = PHASE_RUNTIME_ACTIVE
    else:
        role = RUNTIME_ROLE
        key = "DATABASE_URL"
        source_config = "runtime"
        target_config = "previous"
        allowed_live = {
            PHASE_RUNTIME_ROLE_APPLIED: "previous",
            PHASE_RUNTIME_ACTIVE: "runtime",
        }
        pending_phase = PHASE_RUNTIME_ROLLBACK_PENDING
        completed_phase = PHASE_STAGED

    _require_machine_phase(state, action, set(allowed_live))
    origin_phase = state.phase
    expected_live = allowed_live[origin_phase]
    live_phase = _live_phase(state)
    if live_phase != expected_live:
        _fail(f"role action {action} requires live {expected_live} config; found {live_phase}")
    _verify_phase(
        state,
        source_config,
        container,
        require_live=expected_live == source_config,
    )

    source_dsns = _validate_env(state.files[f"{source_config}.env"])
    target_dsns = _validate_env(state.files[f"{target_config}.env"])
    source_password = source_dsns[key].password
    target_password = target_dsns[key].password
    _confirm(action)
    _set_phase(state, pending_phase, origin_phase)
    try:
        _set_password_while_pending(
            state,
            pending_phase,
            origin_phase,
            container,
            role,
            target_password,
        )
        _replace_live_config(state, f"{target_config}.env")
        _verify_phase(state, target_config, container, require_live=True)
        _set_phase(state, completed_phase)
    except _IndeterminateMutationError as exc:
        raise RotationError(
            f"role action {action} did not reach a determinate database completion; "
            f"it remains indeterminate in {pending_phase}. Keep writers stopped, "
            f"wait at least {RECOVERY_QUIESCENCE_SECONDS} seconds, then run recover"
        ) from exc
    except Exception as exc:
        try:
            _replace_live_config(state, f"{expected_live}.env")
            _set_password_while_pending(
                state,
                pending_phase,
                origin_phase,
                container,
                role,
                source_password,
            )
            _verify_phase(
                state,
                source_config,
                container,
                require_live=expected_live == source_config,
            )
            if _live_phase(state) != expected_live:
                _fail("rollback compensation could not restore the original live config")
            _set_phase(state, origin_phase)
        except Exception as compensation_exc:
            raise RotationError(
                f"role action {action} failed and automatic compensation failed; "
                "writers must remain stopped and recover must be run"
            ) from compensation_exc
        raise RotationError(
            f"role action {action} failed; database and config were restored"
        ) from exc
    _status(f"completed and verified database role action {action}")


def role_password(state_dir: Path, action: str, container: str) -> None:
    with _state_lock(state_dir) as locked_dir:
        state = _load_state(locked_dir)
        if action not in _ROLE_REQUIRED_PHASES:
            _fail("refusing an unknown database role action")
        if state.phase.endswith("-pending"):
            _fail(f"rotation is in pending phase {state.phase}; run recover before any role action")
        # Reject wrong-order actions before any database command, especially
        # before the ALTER ROLE performed by _set_password_with_psql.
        _require_machine_phase(state, action, set(_ROLE_REQUIRED_PHASES[action]))
        _verify_local_role_contract(container)
        if action in {"runtime-new", "migration-new"}:
            _forward_role_password(state, action, container)
        else:
            _rollback_role_password(state, action, container)


def recover(state_dir: Path, container: str) -> None:
    with _state_lock(state_dir) as locked_dir:
        state = _load_state(locked_dir)
        if not state.phase.endswith("-pending") or state.origin_phase is None:
            _fail("recover is only allowed from a pending transition phase")
        _require_recovery_quiescence(state)
        _verify_local_role_contract(container)
        _confirm(f"recover-{state.phase}")
        previous = _validate_env(state.files["previous.env"])
        runtime = _validate_env(state.files["runtime.env"])
        final = _validate_env(state.files["final.env"])

        if state.phase == PHASE_RUNTIME_NEW_PENDING:
            role = RUNTIME_ROLE
            password = previous["DATABASE_URL"].password
            live_config = "previous"
            target_config = "previous"
            target_phase = PHASE_STAGED
        elif state.phase == PHASE_MIGRATION_NEW_PENDING:
            role = MIGRATION_ROLE
            password = runtime["MIGRATION_DATABASE_URL"].password
            live_config = "runtime"
            target_config = "runtime"
            target_phase = PHASE_RUNTIME_ACTIVE
        elif state.phase == PHASE_MIGRATION_ROLLBACK_PENDING:
            role = MIGRATION_ROLE
            password = final["MIGRATION_DATABASE_URL"].password
            live_config = "final" if state.origin_phase == PHASE_FINAL_ACTIVE else "runtime"
            target_config = "final"
            target_phase = state.origin_phase
        else:
            role = RUNTIME_ROLE
            password = runtime["DATABASE_URL"].password
            live_config = "runtime" if state.origin_phase == PHASE_RUNTIME_ACTIVE else "previous"
            target_config = "runtime"
            target_phase = state.origin_phase

        try:
            _set_password_while_pending(
                state,
                state.phase,
                state.origin_phase,
                container,
                role,
                password,
            )
        except _IndeterminateMutationError as exc:
            raise RotationError(
                "recover did not reach a determinate database completion; the "
                "transition remains pending and indeterminate. Keep writers stopped, "
                "wait at least "
                f"{RECOVERY_QUIESCENCE_SECONDS} seconds, then retry recover"
            ) from exc
        _replace_live_config(state, f"{live_config}.env")
        _verify_phase(
            state,
            target_config,
            container,
            require_live=_live_phase(state) == target_config,
        )
        _set_phase(state, target_phase)
        _status(f"recovered pending transition to phase {target_phase}")


def preflight(state_dir: Path, container: str) -> None:
    with _state_lock(state_dir) as locked_dir:
        state = _load_state(locked_dir)
        _require_machine_phase(state, "preflight", {PHASE_STAGED})
        _verify_phase(state, "previous", container, require_live=True)
        _status("preflight passed; no database or service state was changed")


def _assert_rejected(container: str, username: str, password: str) -> None:
    result = _tcp_query(container, username, password, "SELECT 'ACCEPTED';\n")
    if result.status is _ProbeStatus.SUCCESS:
        _fail(f"retired credential still authenticates for role {username}")
    if result.status is not _ProbeStatus.AUTH_REJECTED:
        _fail(f"retired credential probe failed without auth rejection for role {username}")


def _verify_retired_state(state: _State, container: str) -> None:
    previous = _validate_env(state.files["previous.env"])
    _assert_rejected(container, RUNTIME_ROLE, previous["DATABASE_URL"].password)
    _assert_rejected(container, MIGRATION_ROLE, previous["MIGRATION_DATABASE_URL"].password)


def verify_retired(state_dir: Path, container: str) -> None:
    with _state_lock(state_dir) as locked_dir:
        state = _load_state(locked_dir)
        _require_machine_phase(state, "verify-retired", {PHASE_FINAL_ACTIVE})
        _verify_phase(state, "final", container, require_live=True)
        _verify_retired_state(state, container)
        _status("verified both retired database credentials are rejected")


def finalize(state_dir: Path, container: str) -> None:
    with _state_lock(state_dir) as locked_dir:
        state = _load_state(locked_dir)
        _require_machine_phase(state, "finalize", {PHASE_FINAL_ACTIVE})
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
    role_parser.add_argument("action", choices=_ROLE_ACTIONS)
    role_parser.add_argument("--state-dir", required=True, type=Path)
    role_parser.add_argument("--db-container", default=DB_CONTAINER_DEFAULT)

    activate_parser = subparsers.add_parser("activate", help="atomically activate a config")
    activate_parser.add_argument("action", choices=tuple(_ACTIVATIONS))
    activate_parser.add_argument("--state-dir", required=True, type=Path)
    activate_parser.add_argument("--db-container", default=DB_CONTAINER_DEFAULT)

    recover_parser = subparsers.add_parser(
        "recover", help="restore the stable origin of an interrupted transition"
    )
    recover_parser.add_argument("--state-dir", required=True, type=Path)
    recover_parser.add_argument("--db-container", default=DB_CONTAINER_DEFAULT)

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
        activate(args.state_dir, args.action, args.db_container)
    elif args.command == "recover":
        recover(args.state_dir, args.db_container)
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
