"""Unit tests for the CardDAV Basic-auth header decoder + rate limiter.

DB-backed integration tests (full resolve_principal flow) live in
``test_carddav_router.py`` once the testcontainer harness ships.
"""

from __future__ import annotations

import base64
import uuid

import bcrypt
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from contact_ops.carddav.auth import (
    WWW_AUTHENTICATE_HEADER,
    _stamp_last_used,
    _verify_password_any_tenant,
    hash_app_password,
    is_rate_limited,
    parse_basic_auth_header,
    record_failure,
    record_success,
    reset_rate_limits,
    verify_app_password,
)

TENANT_ID = "0ffe658a-0000-4000-8000-000000000001"
APP_CREDENTIAL_ID = "0ffe658a-0000-4000-8000-000000000002"
USER_ID = "uc-carddav-0ffe658a"
PLAINTEXT = "correct-horse-carddav"


def _sync_url(asyncpg_url: str) -> str:
    return asyncpg_url.replace("postgresql+asyncpg://", "postgresql://")


def _runtime_async_url(asyncpg_url: str) -> str:
    return asyncpg_url.replace("postgres:test@", "contact_ops_runtime:rtpw@")


def _ensure_runtime_role(postgres_container: str) -> None:
    eng = create_engine(_sync_url(postgres_container), future=True)
    with eng.begin() as conn:
        conn.execute(
            text(
                "DO $$ BEGIN CREATE ROLE contact_ops_runtime LOGIN PASSWORD 'rtpw' "
                "NOSUPERUSER NOBYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$"
            )
        )
        conn.execute(text("GRANT contact_ops_app TO contact_ops_runtime"))
    eng.dispose()


def _seed_carddav_password(postgres_container: str) -> None:
    eng = create_engine(_sync_url(postgres_container), future=True)
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tenants (
                    id, slug, kind, display_name, owner_user_id,
                    qdrant_namespace, garage_bucket_prefix
                )
                VALUES (
                    CAST(:tenant_id AS uuid), 'carddav-rls', 'brand',
                    'CardDAV RLS', CAST(:tenant_id AS uuid), 'carddav-rls-ns',
                    'carddav-rls-bkt'
                )
                """
            ),
            {"tenant_id": TENANT_ID},
        )
        conn.execute(
            text(
                """
                INSERT INTO user_tenant_membership (user_uc_uid, tenant_id, role, status)
                VALUES (:user_id, CAST(:tenant_id AS uuid), 'member', 'active')
                """
            ),
            {"user_id": USER_ID, "tenant_id": TENANT_ID},
        )
        conn.execute(
            text(
                """
                INSERT INTO carddav_app_passwords (
                    id, tenant_id, user_id, device_label, password_hash, last_4_chars
                )
                VALUES (
                    CAST(:app_credential_id AS uuid), CAST(:tenant_id AS uuid),
                    :user_id, 'iPhone', :password_hash, :last_4
                )
                """
            ),
            {
                "app_credential_id": APP_CREDENTIAL_ID,
                "tenant_id": TENANT_ID,
                "user_id": USER_ID,
                "password_hash": hash_app_password(PLAINTEXT),
                "last_4": PLAINTEXT[-4:],
            },
        )
    eng.dispose()


# ---------- header parsing ----------


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_parse_basic_auth_header_returns_user_password() -> None:
    header = f"Basic {_b64('aaron:secret-token')}"
    result = parse_basic_auth_header(header)
    assert result == ("aaron", "secret-token")


def test_parse_basic_auth_header_accepts_case_insensitive_scheme() -> None:
    header = f"basic {_b64('user:pw')}"
    assert parse_basic_auth_header(header) == ("user", "pw")


def test_parse_basic_auth_header_handles_password_with_colon() -> None:
    header = f"Basic {_b64('user:pw:with:colons')}"
    assert parse_basic_auth_header(header) == ("user", "pw:with:colons")


def test_parse_basic_auth_header_returns_none_when_missing() -> None:
    assert parse_basic_auth_header(None) is None
    assert parse_basic_auth_header("") is None


def test_parse_basic_auth_header_returns_none_on_wrong_scheme() -> None:
    assert parse_basic_auth_header("Bearer abc") is None
    assert parse_basic_auth_header("Digest foo=bar") is None


def test_parse_basic_auth_header_returns_none_on_garbage_base64() -> None:
    assert parse_basic_auth_header("Basic !!!not-valid-base64@@@") is None


def test_parse_basic_auth_header_returns_none_when_no_colon_in_decoded() -> None:
    assert parse_basic_auth_header(f"Basic {_b64('no-colon-here')}") is None


def test_www_authenticate_header_has_realm_and_charset() -> None:
    assert 'realm=' in WWW_AUTHENTICATE_HEADER
    assert 'charset' in WWW_AUTHENTICATE_HEADER


# ---------- bcrypt helpers ----------


def test_hash_and_verify_app_password() -> None:
    plaintext = "test-secret-password-xyz"
    hashed = hash_app_password(plaintext)
    assert hashed.startswith("$2")  # bcrypt prefix
    assert verify_app_password(plaintext, hashed) is True
    assert verify_app_password("wrong-password", hashed) is False


def test_verify_app_password_returns_false_on_malformed_hash() -> None:
    assert verify_app_password("anything", "not-a-bcrypt-hash") is False


def test_hash_app_password_uses_high_cost_factor() -> None:
    h = hash_app_password("x")
    # bcrypt format: $2b$<cost>$<22-char-salt><31-char-digest>
    cost = int(h.split("$")[2])
    assert cost >= 10


@pytest.mark.asyncio
async def test_carddav_lookup_and_stamp_work_as_unbound_runtime_role(
    postgres_container: str,
) -> None:
    """CardDAV auth resolves before tenant binding without weakening table RLS."""

    _ensure_runtime_role(postgres_container)
    _seed_carddav_password(postgres_container)

    engine = create_async_engine(
        _runtime_async_url(postgres_container),
        poolclass=NullPool,
        echo=False,
    )
    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            raw_count = await conn.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM carddav_app_passwords
                    WHERE user_id = :user_id
                    """
                ),
                {"user_id": USER_ID},
            )
            assert raw_count == 0

            session = session_maker(bind=conn, join_transaction_mode="create_savepoint")
            try:
                row, tenant = await _verify_password_any_tenant(
                    session,
                    username=USER_ID,
                    password=PLAINTEXT,
                )
                assert row is not None
                assert tenant is not None
                assert row.id == uuid.UUID(APP_CREDENTIAL_ID)
                assert tenant.id == uuid.UUID(TENANT_ID)
                assert tenant.slug == "carddav-rls"

                await _stamp_last_used(
                    session,
                    row=row,
                    tenant_id=tenant.id,
                    user_agent="pytest-carddav",
                    source_ip="203.0.113.10",
                )
                stamped = (
                    await session.execute(
                        text(
                            """
                            SELECT last_used_at, last_used_user_agent,
                                   last_used_ip::text AS last_used_ip
                            FROM carddav_app_passwords
                            WHERE id = CAST(:app_password_id AS uuid)
                            """
                        ),
                        {"app_password_id": APP_CREDENTIAL_ID},
                    )
                ).mappings().one()
                assert stamped["last_used_at"] is not None
                assert stamped["last_used_user_agent"] == "pytest-carddav"
                assert stamped["last_used_ip"] == "203.0.113.10/32"
            finally:
                await session.close()
        finally:
            await trans.rollback()
    await engine.dispose()


# ---------- rate limiter ----------


def test_rate_limit_threshold_triggers_after_5_failures() -> None:
    reset_rate_limits()
    ip, user = "192.0.2.1", "victim"
    for _ in range(5):
        record_failure(ip, user)
    assert is_rate_limited(ip, user) is True


def test_rate_limit_does_not_apply_to_other_ips() -> None:
    reset_rate_limits()
    for _ in range(5):
        record_failure("attacker", "victim")
    assert is_rate_limited("victim-own-ip", "victim") is False


def test_rate_limit_resets_on_success() -> None:
    reset_rate_limits()
    ip, user = "192.0.2.2", "user"
    for _ in range(4):
        record_failure(ip, user)
    record_success(ip, user)
    assert is_rate_limited(ip, user) is False


def test_rate_limit_isolated_per_user() -> None:
    reset_rate_limits()
    for _ in range(5):
        record_failure("ip", "user-a")
    assert is_rate_limited("ip", "user-a") is True
    assert is_rate_limited("ip", "user-b") is False
