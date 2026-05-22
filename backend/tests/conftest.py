"""
Contact-Ops test configuration.

Session-scoped Postgres container via testcontainers, function-scoped
transaction-rolled-back sessions, role-scoped variants for append-only and
tenant-RLS testing.

Design:
  - `postgres_container` spins up pgvector/pgvector:pg16 once per test session.
  - Alembic migrations are applied once (`alembic upgrade head`).
  - `db_engine` is a session-scoped async engine pointing at that container.
  - `db_session` is a function-scoped fixture that starts a transaction, yields
    an AsyncSession, then rolls back — every test gets a clean slate.
  - `db_session_as_app_role` / `db_session_as_audit_role` SET LOCAL ROLE so
    tests verify the real Postgres privilege boundaries.
  - `db_session_with_tenant` sets `app.tenant_id` GUC so RLS policies
    are exercised.
"""

from __future__ import annotations

import os as _os_module
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

_os_module.environ.setdefault("STANDALONE_MODE", "false")
_os_module.environ.setdefault("ENV", "test")
_os_module.environ.setdefault("KEYCLOAK_ISSUER", "https://test-keycloak.invalid/realms/uchub")
_os_module.environ.setdefault("KEYCLOAK_JWKS_URL", "https://test-keycloak.invalid/protocol/openid-connect/certs")
_os_module.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:test@localhost:5432/contact_ops_test")

_TEST_TENANT_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop for session-scoped async fixtures."""
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _asyncpg_url(sync_url: str) -> str:
    """Convert any Postgres URL to postgresql+asyncpg:// scheme."""
    if "postgresql+psycopg" in sync_url:
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    return sync_url.replace("postgresql://", "postgresql+asyncpg://")


def _sync_url(asyncpg_url: str) -> str:
    return asyncpg_url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="session")
def postgres_container():
    """Postgres container with pgvector, alive for the whole test session."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        "pgvector/pgvector:pg16",
        username="postgres",
        password="test",
        dbname="contact_ops_test",
        driver="psycopg2",
    ) as pg:
        sync_url = pg.get_connection_url()
        _run_migrations(sync_url)
        # Return async-driver URL for the rest of the suite
        yield sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
            "postgresql://", "postgresql+asyncpg://"
        )


def _run_migrations(sync_url: str) -> None:
    """Apply all Alembic migrations to the test database."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    _os_module.environ["MIGRATION_DATABASE_URL"] = sync_url

    alemsrc = Path(__file__).resolve().parent.parent / "alembic"
    cfg = Config(str(alemsrc.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(alemsrc))
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture(scope="session")
async def db_engine(postgres_container: str):
    """Session-scoped async engine pointing at the test Postgres."""
    url = _asyncpg_url(postgres_container)
    engine = create_async_engine(url, poolclass=NullPool, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test transaction-rolled-back session (connects as postgres superuser)."""
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest_asyncio.fixture
async def db_session_as_app_role(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session that SETs LOCAL ROLE contact_ops_app and tenant_id."""
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        await conn.execute(text("SET LOCAL ROLE contact_ops_app"))
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": _TEST_TENANT_ID},
        )
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest_asyncio.fixture
async def db_session_as_audit_role(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session that SETs LOCAL ROLE contact_ops_audit and tenant_id."""
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        await conn.execute(text("SET LOCAL ROLE contact_ops_audit"))
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": _TEST_TENANT_ID},
        )
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest_asyncio.fixture
async def db_session_with_tenant(
    db_engine,
) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session with `app.tenant_id` GUC set for RLS."""
    tenant_id = "00000000-0000-0000-0000-000000000001"
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": tenant_id},
        )
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest_asyncio.fixture
async def seeded_tenants(db_session) -> dict[str, uuid.UUID]:
    """Insert one HIPAA and one non-HIPAA tenant, return their IDs."""
    hipaa_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
    non_hipaa_id = uuid.UUID("00000000-0000-0000-0000-000000000020")

    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix)
            VALUES (:id, 'hipaa-org', 'brand', 'HIPAA Tenant',
                :owner, true, 'hipaa-ns', 'hipaa-bkt')
            """
        ),
        {"id": hipaa_id, "owner": hipaa_id},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix)
            VALUES (:id, 'non-hipaa-org', 'brand', 'Non-HIPAA Tenant',
                :owner, false, 'non-hipaa-ns', 'non-hipaa-bkt')
            """
        ),
        {"id": non_hipaa_id, "owner": non_hipaa_id},
    )
    await db_session.commit()
    return {"hipaa": hipaa_id, "non_hipaa": non_hipaa_id}
