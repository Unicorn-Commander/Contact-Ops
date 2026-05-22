"""Database configuration and session management for Contact-Ops.

Two engines:
- `engine` — connects as `contact_ops_app`, used by all read/write paths
  except audit. RLS policies apply.
- `audit_engine` — connects as `contact_ops_audit`. The only role with INSERT
  privilege on `action_event` once full role separation is in place (currently
  both roles have INSERT; design doc §4.1.9 commits to tightening later).

Tenant context for RLS is set per-session via `SET LOCAL app.tenant_id = ?`
before any query runs. Use `get_tenant_db()` as the FastAPI dependency that
yields a session pre-bound to the calling user's tenant.

Tables and ORM `Base` live in `contact_ops.models`. This module does not
declare any models; never call `Base.metadata.create_all()` — schema is
Alembic-managed only.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool

from contact_ops.core.config import Settings, get_settings

_settings: Settings = get_settings()


def _make_engine(dsn: str):  # type: ignore[no-untyped-def]
    return create_async_engine(
        dsn,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )


engine = _make_engine(_settings.DATABASE_URL)

# Audit engine connects as the `contact_ops_audit` role.
# Defaults to the same DSN as the app when AUDIT_DATABASE_URL is unset (dev);
# in prod, point this at a DSN authenticating as `contact_ops_audit`.
audit_engine = _make_engine(_settings.AUDIT_DATABASE_URL or _settings.DATABASE_URL)

async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

audit_session_maker = async_sessionmaker(
    bind=audit_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an app-role session without tenant binding.

    Prefer `get_tenant_db()` for any query against a tenant-scoped table —
    that dep sets the `app.tenant_id` GUC so RLS policies resolve correctly.
    `get_db()` is for tenant-agnostic operations only (e.g. health probes,
    cross-tenant admin functions guarded elsewhere).
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_tenant_db(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a session pre-bound to the caller's tenant.

    Reads `tenant_id` from `request.state.jwt_claims` (set by
    JWTValidationMiddleware). Issues `SET LOCAL app.tenant_id = ?` before
    yielding, so RLS policies on `persons`, `organizations`, etc. resolve
    the caller's tenant context.

    Raises 401 if no JWT claims are present on the request — the JWT
    middleware should have already rejected unauthenticated requests, so
    reaching here without claims is a programming error, but we fail
    closed regardless.
    """
    claims = getattr(request.state, "jwt_claims", None)
    if not claims or not claims.get("tenant_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="tenant context unresolved",
        )
    tenant_id = str(claims["tenant_id"])

    async with async_session_maker() as session:
        # set_config(name, value, is_local=true) is the parameter-safe way
        # to SET LOCAL — avoids any need for string interpolation.
        await session.execute(
            text("SELECT set_config(:k, :v, true)"),
            {"k": settings.TENANT_GUC_NAME, "v": tenant_id},
        )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager yielding an app-role session.

    For use outside FastAPI (background workers, scripts). Does NOT set
    tenant context — caller is responsible.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_audit_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager yielding an audit-role session.

    The audit middleware uses this to write `action_event` rows under the
    `contact_ops_audit` role, enforcing append-only at the DB layer per
    design doc §4.1.9.
    """
    async with audit_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    """Dispose all engines. Call on application shutdown."""
    await engine.dispose()
    await audit_engine.dispose()
