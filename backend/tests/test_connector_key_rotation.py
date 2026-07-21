from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from contact_ops.connectors.crypto import ConnectorCipher
from contact_ops.connectors.key_rotation_impl import (
    ConnectorKeyRotationError,
    KeyRotationMode,
    KeyRotationReport,
    RotationTimeouts,
    _assert_privileged_connection,
    primary_key_fingerprint,
    run_connector_key_operation,
)
from contact_ops.core.config import Settings


def _settings(
    *,
    legacy_key: str = "",
    keys: tuple[str, ...] = (),
    database_url: str = "postgresql+asyncpg://test:test@localhost/test",
) -> Settings:
    return Settings(
        DATABASE_URL=database_url,
        MIGRATION_DATABASE_URL=database_url,
        ENV="test",
        STANDALONE_MODE=False,
        KEYCLOAK_ISSUER="https://keycloak.invalid/realms/test",
        CONNECTOR_ENCRYPTION_KEY=legacy_key,
        CONNECTOR_ENCRYPTION_KEYS=json.dumps(keys) if keys else "",
        _env_file=None,
    )


def _token(key: str, payload: dict[str, Any]) -> bytes:
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return Fernet(key.encode("ascii")).encrypt(encoded)


def test_legacy_old_only_config_still_reads_and_writes() -> None:
    old_key = Fernet.generate_key().decode("ascii")
    settings = _settings(legacy_key=old_key)
    cipher = ConnectorCipher.from_settings(settings)
    payload = {"provider": "icloud", "refresh_token": "old-only"}

    token = cipher.encrypt_payload(payload)

    assert settings.connector_encryption_keyring == (old_key,)
    assert cipher.decrypt_payload(token) == payload
    assert json.loads(Fernet(old_key.encode()).decrypt(token)) == payload


def test_explicit_ring_takes_precedence_over_legacy_single_key() -> None:
    legacy_key = Fernet.generate_key().decode("ascii")
    primary_key = Fernet.generate_key().decode("ascii")
    fallback_key = Fernet.generate_key().decode("ascii")

    settings = _settings(
        legacy_key=legacy_key,
        keys=(primary_key, fallback_key),
    )

    assert settings.connector_encryption_keyring == (primary_key, fallback_key)


def test_staged_old_primary_reads_both_and_keeps_writing_old() -> None:
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")
    cipher = ConnectorCipher.from_settings(_settings(keys=(old_key, new_key)))
    old_payload = {"generation": "old"}
    new_payload = {"generation": "new"}

    assert cipher.decrypt_payload(_token(old_key, old_payload)) == old_payload
    assert cipher.decrypt_payload(_token(new_key, new_payload)) == new_payload

    staged_write = cipher.encrypt_payload({"stage": "old-primary"})
    assert json.loads(Fernet(old_key.encode()).decrypt(staged_write)) == {"stage": "old-primary"}
    with pytest.raises(InvalidToken):
        Fernet(new_key.encode()).decrypt(staged_write)


def test_new_primary_with_old_fallback_reads_old_and_writes_new() -> None:
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")
    cipher = ConnectorCipher.from_settings(_settings(keys=(new_key, old_key)))
    old_payload = {"generation": "old"}

    assert cipher.decrypt_payload(_token(old_key, old_payload)) == old_payload

    new_write = cipher.encrypt_payload({"stage": "new-primary"})
    assert json.loads(Fernet(new_key.encode()).decrypt(new_write)) == {"stage": "new-primary"}
    with pytest.raises(InvalidToken):
        Fernet(old_key.encode()).decrypt(new_write)


@pytest.mark.parametrize(
    "mode",
    [KeyRotationMode.REWRAP, KeyRotationMode.VERIFY_NEW_ONLY],
)
async def test_reversed_ring_rejected_before_database_access(
    mode: KeyRotationMode,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_key = Fernet.generate_key().decode("ascii")
    intended_new_key = Fernet.generate_key().decode("ascii")
    settings = _settings(keys=(old_key, intended_new_key))
    database_accessed = False

    def forbidden_engine(*_args: Any, **_kwargs: Any) -> None:
        nonlocal database_accessed
        database_accessed = True
        raise AssertionError("primary-order guard must run before database access")

    monkeypatch.setattr(
        "contact_ops.connectors.key_rotation_impl.create_async_engine",
        forbidden_engine,
    )

    with pytest.raises(ConnectorKeyRotationError, match="expected fingerprint"):
        await run_connector_key_operation(
            settings=settings,
            mode=mode,
            actor="pytest",
            expected_primary_sha256=primary_key_fingerprint(intended_new_key),
        )

    assert database_accessed is False


async def _insert_rotation_fixture(
    db_engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    slug: str,
    payloads: tuple[tuple[str, bytes], ...],
) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO tenants
                    (id, slug, kind, display_name, owner_user_id,
                     qdrant_namespace, garage_bucket_prefix)
                VALUES
                    (CAST(:id AS uuid), :slug, CAST('brand' AS tenant_kind),
                     :display_name, CAST(:owner AS uuid), :qdrant, :garage)
                """
            ),
            {
                "id": str(tenant_id),
                "slug": slug,
                "display_name": slug,
                "owner": str(tenant_id),
                "qdrant": f"q-{slug}",
                "garage": f"g-{slug}",
            },
        )
        for provider, token in payloads:
            await connection.execute(
                text(
                    """
                    INSERT INTO connector_configs
                        (tenant_id, provider, display_name, configured_by,
                         encrypted_payload)
                    VALUES
                        (CAST(:tenant AS uuid), :provider, :provider,
                         'rotation-test', :token)
                    """
                ),
                {
                    "tenant": str(tenant_id),
                    "provider": provider,
                    "token": token,
                },
            )


async def _delete_rotation_fixture(db_engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM connector_configs WHERE tenant_id = CAST(:tenant AS uuid)"),
            {"tenant": str(tenant_id)},
        )
        await connection.execute(
            text("DELETE FROM tenants WHERE id = CAST(:tenant AS uuid)"),
            {"tenant": str(tenant_id)},
        )


async def _wait_for_lock_wait(
    db_engine: AsyncEngine,
    *,
    application_name: str,
    timeout_seconds: float = 1.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        async with db_engine.connect() as connection:
            waiting = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_stat_activity
                        WHERE application_name = :application_name
                          AND wait_event_type = 'Lock'
                    )
                    """
                ),
                {"application_name": application_name},
            )
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("rotation never entered the expected table-lock wait")


@pytest.mark.integration
async def test_runtime_database_role_is_not_authorized_for_global_rewrap(
    db_engine: AsyncEngine,
) -> None:
    async with db_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(text("SET LOCAL ROLE contact_ops_app"))
            with pytest.raises(ConnectorKeyRotationError):
                await _assert_privileged_connection(connection)


@pytest.mark.integration
async def test_dry_run_full_rewrap_and_verify_new_only(
    db_engine: AsyncEngine,
    postgres_container: str,
) -> None:
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")
    tenant_id = uuid.UUID("30000000-0000-0000-0000-000000000001")
    old_payload = {"provider": "icloud", "nested": {"refresh": "preserved"}}
    new_payload = {"provider": "gmail", "scopes": ["contacts.readonly"]}
    settings = _settings(keys=(new_key, old_key), database_url=postgres_container)
    expected_primary = primary_key_fingerprint(new_key)

    await _insert_rotation_fixture(
        db_engine,
        tenant_id=tenant_id,
        slug="connector-rotation-full",
        payloads=(
            ("icloud", _token(old_key, old_payload)),
            ("gmail", _token(new_key, new_payload)),
        ),
    )
    try:
        dry_run = await run_connector_key_operation(
            settings=settings,
            mode=KeyRotationMode.DRY_RUN,
            actor="pytest",
        )
        assert dry_run == KeyRotationReport(
            mode=KeyRotationMode.DRY_RUN,
            total=2,
            already_primary=1,
            needs_rewrap=1,
            rewrapped=0,
        )

        with pytest.raises(ConnectorKeyRotationError):
            await run_connector_key_operation(
                settings=settings,
                mode=KeyRotationMode.VERIFY_NEW_ONLY,
                actor="pytest",
                expected_primary_sha256=expected_primary,
            )

        rewrapped = await run_connector_key_operation(
            settings=settings,
            mode=KeyRotationMode.REWRAP,
            actor="pytest",
            expected_primary_sha256=expected_primary,
        )
        assert rewrapped.rewrapped == 1
        assert rewrapped.already_primary == 1

        verified = await run_connector_key_operation(
            settings=settings,
            mode=KeyRotationMode.VERIFY_NEW_ONLY,
            actor="pytest",
            expected_primary_sha256=expected_primary,
        )
        assert verified.total == 2
        assert verified.already_primary == 2
        assert verified.needs_rewrap == 0

        async with db_engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT provider, encrypted_payload
                        FROM connector_configs
                        WHERE tenant_id = CAST(:tenant AS uuid)
                        ORDER BY provider
                        """
                    ),
                    {"tenant": str(tenant_id)},
                )
            ).all()
        decoded = {
            str(row.provider): json.loads(
                Fernet(new_key.encode()).decrypt(bytes(row.encrypted_payload))
            )
            for row in rows
        }
        assert decoded == {"gmail": new_payload, "icloud": old_payload}
    finally:
        await _delete_rotation_fixture(db_engine, tenant_id)


@pytest.mark.integration
async def test_corrupt_row_aborts_and_rolls_back_every_rewrap(
    db_engine: AsyncEngine,
    postgres_container: str,
) -> None:
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")
    tenant_id = uuid.UUID("30000000-0000-0000-0000-000000000002")
    old_token = _token(old_key, {"must": "remain-old"})
    corrupt_token = b"not-an-authenticated-fernet-token"
    settings = _settings(keys=(new_key, old_key), database_url=postgres_container)
    expected_primary = primary_key_fingerprint(new_key)

    await _insert_rotation_fixture(
        db_engine,
        tenant_id=tenant_id,
        slug="connector-rotation-corrupt",
        payloads=(("icloud", old_token), ("gmail", corrupt_token)),
    )
    try:
        with pytest.raises(ConnectorKeyRotationError):
            await run_connector_key_operation(
                settings=settings,
                mode=KeyRotationMode.REWRAP,
                actor="pytest",
                expected_primary_sha256=expected_primary,
            )

        async with db_engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT provider, encrypted_payload
                        FROM connector_configs
                        WHERE tenant_id = CAST(:tenant AS uuid)
                        """
                    ),
                    {"tenant": str(tenant_id)},
                )
            ).all()
        stored = {str(row.provider): bytes(row.encrypted_payload) for row in rows}
        assert stored == {"icloud": old_token, "gmail": corrupt_token}
        with pytest.raises(InvalidToken):
            Fernet(new_key.encode()).decrypt(stored["icloud"])
    finally:
        await _delete_rotation_fixture(db_engine, tenant_id)


@pytest.mark.integration
async def test_lock_timeout_is_bounded_and_releases_queued_writer(
    db_engine: AsyncEngine,
    postgres_container: str,
) -> None:
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")
    tenant_id = uuid.UUID("30000000-0000-0000-0000-000000000003")
    old_token = _token(old_key, {"generation": "old"})
    settings = _settings(keys=(new_key, old_key), database_url=postgres_container)
    expected_primary = primary_key_fingerprint(new_key)
    application_name = "contact-ops-connector-rewrap:pytest-timeout"

    await _insert_rotation_fixture(
        db_engine,
        tenant_id=tenant_id,
        slug="connector-rotation-timeout",
        payloads=(("icloud", old_token),),
    )

    blocker_connection = await db_engine.connect()
    blocker_transaction = await blocker_connection.begin()
    rotation_task: asyncio.Task[KeyRotationReport] | None = None
    writer_task: asyncio.Task[None] | None = None
    try:
        # This ordinary writer holds ROW EXCLUSIVE. The rotation's stronger
        # SHARE ROW EXCLUSIVE must wait, and PostgreSQL queues a later compatible
        # writer behind that waiter until the bounded lock timeout releases it.
        await blocker_connection.execute(
            text(
                """
                UPDATE connector_configs
                SET display_name = 'held-by-writer'
                WHERE tenant_id = CAST(:tenant AS uuid)
                  AND provider = 'icloud'
                """
            ),
            {"tenant": str(tenant_id)},
        )

        started = time.monotonic()
        rotation_task = asyncio.create_task(
            run_connector_key_operation(
                settings=settings,
                mode=KeyRotationMode.REWRAP,
                actor="pytest-timeout",
                expected_primary_sha256=expected_primary,
                timeouts=RotationTimeouts(
                    lock_timeout_ms=250,
                    statement_timeout_ms=2_000,
                ),
            )
        )
        await _wait_for_lock_wait(
            db_engine,
            application_name=application_name,
            timeout_seconds=1.0,
        )

        async def queued_writer() -> None:
            async with db_engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO connector_configs
                            (tenant_id, provider, display_name, configured_by,
                             encrypted_payload)
                        VALUES
                            (CAST(:tenant AS uuid), 'gmail', 'queued-writer',
                             'rotation-test', :token)
                        """
                    ),
                    {
                        "tenant": str(tenant_id),
                        "token": _token(new_key, {"generation": "new"}),
                    },
                )

        writer_task = asyncio.create_task(queued_writer())

        with pytest.raises(ConnectorKeyRotationError) as failure:
            await asyncio.wait_for(rotation_task, timeout=1.5)
        assert str(failure.value) == ("connector key operation failed; transaction rolled back")
        assert time.monotonic() - started < 1.5

        # The first writer is intentionally still open. Once the timed-out
        # rotation leaves the lock queue, the compatible queued writer must run
        # promptly instead of remaining stranded behind it.
        await asyncio.wait_for(writer_task, timeout=1.0)

        await blocker_transaction.rollback()
        await blocker_connection.close()

        async with db_engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT provider, encrypted_payload
                        FROM connector_configs
                        WHERE tenant_id = CAST(:tenant AS uuid)
                        ORDER BY provider
                        """
                    ),
                    {"tenant": str(tenant_id)},
                )
            ).all()
        stored = {str(row.provider): bytes(row.encrypted_payload) for row in rows}
        assert stored["icloud"] == old_token
        assert set(stored) == {"gmail", "icloud"}
    finally:
        if rotation_task is not None and not rotation_task.done():
            rotation_task.cancel()
            with suppress(asyncio.CancelledError):
                await rotation_task
        if writer_task is not None and not writer_task.done():
            writer_task.cancel()
            with suppress(asyncio.CancelledError):
                await writer_task
        if blocker_transaction.is_active:
            await blocker_transaction.rollback()
        if not blocker_connection.closed:
            await blocker_connection.close()
        await _delete_rotation_fixture(db_engine, tenant_id)


def test_module_entrypoint_contains_settings_failure_and_unrelated_secret(
    tmp_path: Path,
) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    unrelated_marker = "postmark-marker-must-never-render"
    process_env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(backend_root),
        "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/test",
        "ENV": "prod",
        # This deliberate validation failure occurs while unrelated secret
        # input is also present. An uncaught Pydantic traceback renders the
        # entire input_value dictionary, which is the regression being fenced.
        "STANDALONE_MODE": "true",
        "POSTMARK_SERVER_TOKEN": unrelated_marker,
    }

    completed = subprocess.run(  # noqa: S603 - fixed interpreter and module arguments.
        [
            sys.executable,
            "-m",
            "contact_ops.connectors.key_rotation",
            "--dry-run",
            "--actor",
            "pytest",
        ],
        cwd=tmp_path,
        env=process_env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == ("ERROR: connector key operation failed; transaction rolled back\n")
    assert unrelated_marker not in completed.stdout
    assert unrelated_marker not in completed.stderr
    assert "Traceback" not in completed.stderr
