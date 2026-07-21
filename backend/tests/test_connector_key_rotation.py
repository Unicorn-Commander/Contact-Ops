from __future__ import annotations

import json
import uuid
from collections.abc import Coroutine
from typing import Any

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from contact_ops.connectors.crypto import ConnectorCipher
from contact_ops.connectors.key_rotation import (
    ConnectorKeyRotationError,
    KeyRotationMode,
    KeyRotationReport,
    _assert_privileged_connection,
    main,
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
            )

        rewrapped = await run_connector_key_operation(
            settings=settings,
            mode=KeyRotationMode.REWRAP,
            actor="pytest",
        )
        assert rewrapped.rewrapped == 1
        assert rewrapped.already_primary == 1

        verified = await run_connector_key_operation(
            settings=settings,
            mode=KeyRotationMode.VERIFY_NEW_ONLY,
            actor="pytest",
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


def test_cli_failure_never_outputs_exception_keys_or_payloads(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_key = Fernet.generate_key().decode("ascii")
    payload_marker = "payload-marker-that-must-not-print"

    async def fail_operation(**_kwargs: Any) -> KeyRotationReport:
        raise AssertionError("the patched event-loop runner should intercept this")

    def fail_without_closing_session_loop(
        coroutine: Coroutine[Any, Any, KeyRotationReport],
    ) -> KeyRotationReport:
        # pytest-asyncio owns a session-scoped loop. Calling the real
        # asyncio.run() from this synchronous test would clear that loop and
        # cascade failures through every later async test in the full suite.
        coroutine.close()
        raise RuntimeError(f"{secret_key} {payload_marker}")

    monkeypatch.setattr(
        "contact_ops.connectors.key_rotation.get_settings",
        lambda: object(),
    )
    monkeypatch.setattr(
        "contact_ops.connectors.key_rotation.run_connector_key_operation",
        fail_operation,
    )
    monkeypatch.setattr(
        "contact_ops.connectors.key_rotation.asyncio.run",
        fail_without_closing_session_loop,
    )

    exit_code = main(["--dry-run", "--actor", "pytest"])
    output = capsys.readouterr()

    assert exit_code == 2
    assert secret_key not in output.out
    assert secret_key not in output.err
    assert payload_marker not in output.out
    assert payload_marker not in output.err
    assert "transaction rolled back" in output.err
