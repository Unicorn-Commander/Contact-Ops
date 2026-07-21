"""Privileged, transactional connector-payload key rotation operator CLI.

The command intentionally accepts no key arguments: keys come only from the
shared :class:`contact_ops.core.config.Settings` contract, keeping them out of
shell history and process listings. Reaching the data requires an authenticated
``MIGRATION_DATABASE_URL`` role with ``SUPERUSER`` or ``BYPASSRLS``; the normal
tenant runtime role is refused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from contact_ops.connectors.crypto import ConnectorCipher, ConnectorPayloadError
from contact_ops.core.config import Settings, get_settings


class KeyRotationMode(StrEnum):
    DRY_RUN = "dry-run"
    REWRAP = "rewrap"
    VERIFY_NEW_ONLY = "verify-new-only"


class ConnectorKeyRotationError(RuntimeError):
    """Fail-closed rotation error with no key, ciphertext, or plaintext detail."""


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
) -> KeyRotationReport:
    """Run one authenticated connector-key operation in an owned transaction."""

    if not _ACTOR_PATTERN.fullmatch(actor):
        raise ConnectorKeyRotationError("operator actor must be 1-40 safe identity characters")
    cipher = ConnectorCipher.from_settings(settings)
    engine = create_async_engine(
        _admin_async_url(settings),
        # READ COMMITTED is intentional: authentication happens before the
        # rewrap table lock, so the first connector SELECT must take a fresh
        # snapshot *after* that lock is acquired. REPEATABLE READ would pin the
        # snapshot on the earlier pg_roles query and could miss a row committed
        # while the command was waiting for the lock.
        isolation_level="READ COMMITTED",
    )
    try:
        async with engine.begin() as connection:
            await _assert_privileged_connection(connection)
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
    finally:
        await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
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
    args = _parser().parse_args(argv)
    try:
        report = asyncio.run(
            run_connector_key_operation(
                settings=get_settings(),
                mode=_mode_from_args(args),
                actor=str(args.actor),
            )
        )
    except Exception:  # noqa: BLE001 - deliberately suppress secret-bearing exception reprs.
        sys.stderr.write("ERROR: connector key operation failed; transaction rolled back\n")
        return 2
    sys.stdout.write(f"{_format_report(report)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
