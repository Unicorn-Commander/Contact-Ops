"""Connector-payload key rotation implementation and operator CLI.

The command intentionally accepts no key arguments: keys come only from the
shared :class:`contact_ops.core.config.Settings` contract, keeping them out of
shell history and process listings. Reaching the data requires an authenticated
``MIGRATION_DATABASE_URL`` role with ``SUPERUSER`` or ``BYPASSRLS``; the normal
tenant runtime role is refused.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Never

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from contact_ops.connectors.crypto import ConnectorCipher, ConnectorPayloadError
from contact_ops.core.config import Settings, get_settings


class KeyRotationMode(StrEnum):
    DRY_RUN = "dry-run"
    REWRAP = "rewrap"
    VERIFY_NEW_ONLY = "verify-new-only"


class ConnectorKeyRotationError(RuntimeError):
    """Fail-closed rotation error with no key, ciphertext, or plaintext detail."""


class _SecretSafeArgumentParser(argparse.ArgumentParser):
    """Discard argparse messages because they can contain rejected values."""

    def error(self, _message: str) -> Never:
        raise ConnectorKeyRotationError("connector key operation arguments are invalid")


@dataclass(frozen=True)
class KeyRotationReport:
    mode: KeyRotationMode
    total: int
    already_primary: int
    needs_rewrap: int
    rewrapped: int


@dataclass(frozen=True)
class _PreparedRow:
    connector_id: str
    old_token: bytes
    payload: dict[str, Any]
    already_primary: bool
    new_token: bytes | None = None


_ACTOR_PATTERN = re.compile(r"^[A-Za-z0-9_.:@/-]{1,40}$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_DEFAULT_LOCK_TIMEOUT_MS = 5_000
_DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
_MIN_TIMEOUT_MS = 100
_MAX_LOCK_TIMEOUT_MS = 60_000
_MAX_STATEMENT_TIMEOUT_MS = 300_000


@dataclass(frozen=True)
class RotationTimeouts:
    lock_timeout_ms: int = _DEFAULT_LOCK_TIMEOUT_MS
    statement_timeout_ms: int = _DEFAULT_STATEMENT_TIMEOUT_MS

    def validate(self) -> None:
        if not _MIN_TIMEOUT_MS <= self.lock_timeout_ms <= _MAX_LOCK_TIMEOUT_MS:
            raise ConnectorKeyRotationError("connector lock timeout is outside safe bounds")
        if not _MIN_TIMEOUT_MS <= self.statement_timeout_ms <= _MAX_STATEMENT_TIMEOUT_MS:
            raise ConnectorKeyRotationError("connector statement timeout is outside safe bounds")
        if self.statement_timeout_ms <= self.lock_timeout_ms:
            raise ConnectorKeyRotationError(
                "connector statement timeout must exceed the lock timeout"
            )


def primary_key_fingerprint(key: str) -> str:
    """Return the non-secret SHA-256 fingerprint of a Fernet key string."""

    return hashlib.sha256(key.encode("ascii")).hexdigest()


def _assert_expected_primary(
    *,
    keys: tuple[str, ...],
    mode: KeyRotationMode,
    expected_primary_sha256: str | None,
) -> None:
    guarded_modes = {KeyRotationMode.REWRAP, KeyRotationMode.VERIFY_NEW_ONLY}
    if mode not in guarded_modes and expected_primary_sha256 is None:
        return
    if expected_primary_sha256 is None or not _FINGERPRINT_PATTERN.fullmatch(
        expected_primary_sha256
    ):
        raise ConnectorKeyRotationError("a valid expected primary SHA-256 fingerprint is required")
    if not keys:
        raise ConnectorKeyRotationError("connector encryption requires a configured key")
    actual = primary_key_fingerprint(keys[0])
    if not hmac.compare_digest(actual, expected_primary_sha256.lower()):
        raise ConnectorKeyRotationError(
            "configured primary connector key does not match the expected fingerprint"
        )


def _admin_async_url(settings: Settings) -> str:
    raw = settings.MIGRATION_DATABASE_URL
    if not raw:
        raise ConnectorKeyRotationError(
            "MIGRATION_DATABASE_URL is required for connector key operations"
        )
    _scheme, separator, rest = raw.partition("://")
    if not separator:
        raise ConnectorKeyRotationError("MIGRATION_DATABASE_URL is invalid")
    return f"postgresql+asyncpg://{rest}"


async def _assert_privileged_connection(connection: AsyncConnection) -> None:
    """Require database-authenticated cross-tenant authority.

    Connector rows are protected by forced RLS. A runtime login, even one that
    knows the application DSN, must not be able to enumerate or rewrite every
    tenant. Only a superuser or explicit BYPASSRLS operator role may proceed.
    """

    row = (
        await connection.execute(
            text(
                """
                SELECT rolsuper, rolbypassrls
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
        )
    ).one_or_none()
    if row is None or not (bool(row.rolsuper) or bool(row.rolbypassrls)):
        raise ConnectorKeyRotationError(
            "database role is not authorized for cross-tenant connector key operations"
        )


async def _set_transaction_timeouts(
    connection: AsyncConnection,
    timeouts: RotationTimeouts,
) -> None:
    """Bound lock acquisition and every later statement for this transaction."""

    timeouts.validate()
    # Values are validated bounded integers before interpolation. PostgreSQL
    # does not accept bind parameters in SET LOCAL syntax.
    await connection.execute(text(f"SET LOCAL lock_timeout = '{timeouts.lock_timeout_ms}ms'"))
    await connection.execute(
        text(f"SET LOCAL statement_timeout = '{timeouts.statement_timeout_ms}ms'")
    )


def _semantic_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical JSON used only to prove rewrap preserved semantic plaintext."""

    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConnectorKeyRotationError("connector payload is not valid canonical JSON") from exc


def _prepare_row(
    *,
    connector_id: str,
    token: bytes,
    cipher: ConnectorCipher,
    mode: KeyRotationMode,
) -> _PreparedRow:
    try:
        if mode is KeyRotationMode.VERIFY_NEW_ONLY:
            payload = cipher.decrypt_payload_with_primary(token)
            return _PreparedRow(connector_id, token, payload, already_primary=True)

        payload = cipher.decrypt_payload(token)
        semantic_before = _semantic_bytes(payload)
        try:
            primary_payload = cipher.decrypt_payload_with_primary(token)
        except ConnectorPayloadError:
            primary_payload = None

        if primary_payload is not None:
            if _semantic_bytes(primary_payload) != semantic_before:
                raise ConnectorKeyRotationError(
                    "primary-key verification changed connector payload semantics"
                )
            return _PreparedRow(connector_id, token, payload, already_primary=True)

        if mode is KeyRotationMode.DRY_RUN:
            return _PreparedRow(connector_id, token, payload, already_primary=False)

        new_token = cipher.encrypt_payload(payload)
        verified = cipher.decrypt_payload_with_primary(new_token)
        if _semantic_bytes(verified) != semantic_before:
            raise ConnectorKeyRotationError(
                "rewrap verification changed connector payload semantics"
            )
        return _PreparedRow(
            connector_id,
            token,
            payload,
            already_primary=False,
            new_token=new_token,
        )
    except ConnectorKeyRotationError:
        raise
    except ConnectorPayloadError as exc:
        raise ConnectorKeyRotationError(
            f"connector {connector_id} failed authenticated payload verification"
        ) from exc


async def rotate_connector_payloads(
    connection: AsyncConnection,
    *,
    cipher: ConnectorCipher,
    mode: KeyRotationMode,
) -> KeyRotationReport:
    """Inspect, rewrap, or primary-only verify every connector in one transaction.

    The caller owns the surrounding transaction. Rewrap and primary-only verify
    take a table-level ``SHARE ROW EXCLUSIVE`` lock: reads continue, while
    connector creates/token refreshes wait briefly so an old-primary writer
    cannot race behind the final verification. Every row is decrypted and every
    replacement is verified before the first UPDATE. Any corrupt row or failed
    compare-and-swap raises, causing the owning transaction to roll back all
    writes.
    """

    if mode in {KeyRotationMode.REWRAP, KeyRotationMode.VERIFY_NEW_ONLY}:
        await connection.execute(text("LOCK TABLE connector_configs IN SHARE ROW EXCLUSIVE MODE"))

    result = await connection.execute(
        text(
            """
            SELECT id, encrypted_payload
            FROM connector_configs
            ORDER BY id
            """
        )
    )
    prepared = [
        _prepare_row(
            connector_id=str(row.id),
            token=bytes(row.encrypted_payload),
            cipher=cipher,
            mode=mode,
        )
        for row in result
    ]

    already_primary = sum(row.already_primary for row in prepared)
    needs_rewrap = len(prepared) - already_primary

    if mode is not KeyRotationMode.REWRAP:
        return KeyRotationReport(
            mode=mode,
            total=len(prepared),
            already_primary=already_primary,
            needs_rewrap=needs_rewrap,
            rewrapped=0,
        )

    for prepared_row in prepared:
        if prepared_row.already_primary:
            continue
        if prepared_row.new_token is None:
            raise ConnectorKeyRotationError("rewrap preflight did not produce a new token")
        updated = await connection.execute(
            text(
                """
                UPDATE connector_configs
                SET encrypted_payload = :new_token
                WHERE id = CAST(:connector_id AS uuid)
                  AND encrypted_payload = :old_token
                """
            ),
            {
                "connector_id": prepared_row.connector_id,
                "old_token": prepared_row.old_token,
                "new_token": prepared_row.new_token,
            },
        )
        if updated.rowcount != 1:
            raise ConnectorKeyRotationError(
                f"connector {prepared_row.connector_id} changed during rewrap"
            )

    # Verify what is actually in the locked table, not merely the generated
    # replacement values, before the transaction is allowed to commit.
    verify_result = await connection.execute(
        text("SELECT id, encrypted_payload FROM connector_configs ORDER BY id")
    )
    verified_count = 0
    for verify_row in verify_result:
        try:
            cipher.decrypt_payload_with_primary(bytes(verify_row.encrypted_payload))
        except ConnectorPayloadError as exc:
            raise ConnectorKeyRotationError(
                f"connector {verify_row.id} failed post-rewrap primary-key verification"
            ) from exc
        verified_count += 1
    if verified_count != len(prepared):
        raise ConnectorKeyRotationError("connector row count changed during rewrap")

    return KeyRotationReport(
        mode=mode,
        total=len(prepared),
        already_primary=already_primary,
        needs_rewrap=needs_rewrap,
        rewrapped=needs_rewrap,
    )


async def run_connector_key_operation(
    *,
    settings: Settings,
    mode: KeyRotationMode,
    actor: str,
    expected_primary_sha256: str | None = None,
    timeouts: RotationTimeouts | None = None,
) -> KeyRotationReport:
    """Run one authenticated connector-key operation in an owned transaction."""

    engine: AsyncEngine | None = None
    try:
        if not _ACTOR_PATTERN.fullmatch(actor):
            raise ConnectorKeyRotationError("operator actor must be 1-40 safe identity characters")
        effective_timeouts = timeouts or RotationTimeouts()
        effective_timeouts.validate()
        keys = settings.connector_encryption_keyring
        _assert_expected_primary(
            keys=keys,
            mode=mode,
            expected_primary_sha256=expected_primary_sha256,
        )
        cipher = ConnectorCipher.from_keys(keys)
        engine = create_async_engine(
            _admin_async_url(settings),
            # READ COMMITTED is intentional: authentication happens before the
            # rewrap table lock, so the first connector SELECT must take a fresh
            # snapshot *after* that lock is acquired. REPEATABLE READ would pin the
            # snapshot on the earlier pg_roles query and could miss a row committed
            # while the command was waiting for the lock.
            isolation_level="READ COMMITTED",
        )
        async with engine.begin() as connection:
            await _assert_privileged_connection(connection)
            await _set_transaction_timeouts(connection, effective_timeouts)
            application_name = f"contact-ops-connector-{mode.value}:{actor}"
            await connection.execute(
                text("SELECT set_config('application_name', :name, true)"),
                {"name": application_name},
            )
            return await rotate_connector_payloads(
                connection,
                cipher=cipher,
                mode=mode,
            )
    except ConnectorKeyRotationError:
        raise
    except Exception as exc:
        raise ConnectorKeyRotationError(
            "connector key operation failed; transaction rolled back"
        ) from exc
    finally:
        if engine is not None:
            await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = _SecretSafeArgumentParser(
        prog="python -m contact_ops.connectors.key_rotation",
        description="Authenticated connector payload key rewrap and verification",
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="decrypt every row and report primary/fallback counts without writes",
    )
    modes.add_argument(
        "--rewrap",
        action="store_true",
        help="transactionally re-encrypt fallback rows with the primary key",
    )
    modes.add_argument(
        "--verify-new-only",
        action="store_true",
        help="require every row to decrypt with only the primary key",
    )
    parser.add_argument(
        "--actor",
        required=True,
        help="operator identity for the authenticated database session label",
    )
    parser.add_argument(
        "--expected-primary-sha256",
        help=("required for rewrap/verify; SHA-256 of the intended primary Fernet key string"),
    )
    parser.add_argument(
        "--lock-timeout-ms",
        type=int,
        default=_DEFAULT_LOCK_TIMEOUT_MS,
        help=(
            "transaction-local connector table lock timeout "
            f"({_MIN_TIMEOUT_MS}-{_MAX_LOCK_TIMEOUT_MS}; default: "
            f"{_DEFAULT_LOCK_TIMEOUT_MS})"
        ),
    )
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=_DEFAULT_STATEMENT_TIMEOUT_MS,
        help=(
            "transaction-local statement timeout "
            f"({_MIN_TIMEOUT_MS}-{_MAX_STATEMENT_TIMEOUT_MS}; default: "
            f"{_DEFAULT_STATEMENT_TIMEOUT_MS})"
        ),
    )
    return parser


def _mode_from_args(args: argparse.Namespace) -> KeyRotationMode:
    if args.rewrap:
        return KeyRotationMode.REWRAP
    if args.verify_new_only:
        return KeyRotationMode.VERIFY_NEW_ONLY
    return KeyRotationMode.DRY_RUN


def _format_report(report: KeyRotationReport) -> str:
    if report.mode is KeyRotationMode.DRY_RUN:
        return (
            "OK dry-run: "
            f"total={report.total} primary={report.already_primary} "
            f"fallback={report.needs_rewrap}; no rows changed"
        )
    if report.mode is KeyRotationMode.VERIFY_NEW_ONLY:
        return f"OK verify-new-only: total={report.total} primary={report.already_primary}"
    return (
        "OK rewrap: "
        f"total={report.total} rewrapped={report.rewrapped} "
        f"already_primary={report.already_primary}; transaction committed"
    )


def main(argv: list[str] | None = None) -> int:
    try:
        # Parsing belongs inside the secret-safe boundary. The custom parser
        # drops argparse's rejected-value message, while argparse's ordinary
        # ``--help`` SystemExit(0) remains untouched.
        args = _parser().parse_args(argv)
        report = asyncio.run(
            run_connector_key_operation(
                settings=get_settings(),
                mode=_mode_from_args(args),
                actor=str(args.actor),
                expected_primary_sha256=args.expected_primary_sha256,
                timeouts=RotationTimeouts(
                    lock_timeout_ms=args.lock_timeout_ms,
                    statement_timeout_ms=args.statement_timeout_ms,
                ),
            )
        )
    except Exception:  # noqa: BLE001 - deliberately suppress secret-bearing exception reprs.
        sys.stderr.write("ERROR: connector key operation failed; transaction rolled back\n")
        return 2
    sys.stdout.write(f"{_format_report(report)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
