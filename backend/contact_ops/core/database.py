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

import json
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
        # Fail fast instead of blocking forever when the pool is exhausted — the
        # default pool_timeout is None (infinite wait), so a stalled DB/FalkorDB
        # partition would hang request N+1 indefinitely with no signal.
        pool_timeout=10,
        echo=False,
        # JSONB columns (e.g. action_event.payload) routinely hold UUID / datetime
        # / Decimal values; the stdlib default serializer raises on those, which
        # aborted every importer child-write (add_email/add_phone emit an
        # action_event whose payload carries a UUID). default=str makes JSONB
        # serialization total without changing output for plain JSON types.
        json_serializer=lambda obj: json.dumps(obj, default=str),
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


async def bind_session_context(
    session: AsyncSession,
    tenant_id: str,
    uc_uid: str | None,
    settings: Settings | None = None,
) -> None:
    """Bind RLS session context on ``session`` in one place.

    Sets the ``app.tenant_id`` GUC and — when ``uc_uid`` is provided — the
    ``app.uc_uid`` GUC, both as ``SET LOCAL`` (transaction-scoped) via the
    parameter-safe ``set_config(name, value, is_local=true)`` idiom. This is
    the single chokepoint every set-point routes through so the two GUCs are
    always set together and no call site can drift.

    ``tenant_id`` is required (RLS policies key off ``app.tenant_id``).
    ``uc_uid`` is optional only because no RLS policy references it yet in
    Phase 4.0a; callers should still pass a value (a real uc_uid, or a
    ``service:<name>`` identity for pure service/CLI paths) so attribution is
    never silently lost.
    """
    cfg = settings or _settings
    await session.execute(
        text("SELECT set_config(:k, :v, true)"),
        {"k": cfg.TENANT_GUC_NAME, "v": str(tenant_id)},
    )
    if uc_uid:
        await session.execute(
            text("SELECT set_config(:k, :v, true)"),
            {"k": cfg.UC_UID_GUC_NAME, "v": str(uc_uid)},
        )


async def _user_is_active_member(
    session: AsyncSession, uc_uid: str, tenant_id: str
) -> bool:
    """Membership-gate predicate (design §4.4).

    Calls the ``user_is_active_member`` SECURITY DEFINER function (migration 0037)
    so the check is correct regardless of the session's RLS/GUC state. Fail-closed:
    an empty uc_uid resolves to False.
    """
    if not uc_uid:
        return False
    result = await session.execute(
        text("SELECT user_is_active_member(:u, CAST(:t AS uuid))"),
        {"u": uc_uid, "t": tenant_id},
    )
    return bool(result.scalar())


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
    # uc_uid is normalized to a non-empty value in JWTValidationMiddleware
    # (falls back to `sub`); read it from the same claims source as tenant_id.
    uc_uid = claims.get("uc_uid") or claims.get("sub")

    async with async_session_maker() as session:
        # set_config(name, value, is_local=true) is the parameter-safe way
        # to SET LOCAL — avoids any need for string interpolation. Both the
        # tenant and uc_uid GUCs are set via the shared bind helper so they
        # never drift apart.
        await bind_session_context(session, tenant_id, uc_uid, settings)
        # Membership gate (design §4.4): a human Keycloak session must hold an
        # active user_tenant_membership for its tenant. Flag-gated (off until
        # memberships are seeded, else it locks everyone out) and scoped to the
        # Keycloak issuer — PAT, Brigade, and standalone use their own validation
        # paths. Fail-closed.
        if (
            settings.MEMBERSHIP_GATE_ENFORCED
            and claims.get("iss") == settings.KEYCLOAK_ISSUER
            and not await _user_is_active_member(session, uc_uid, tenant_id)
        ):
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not a member of this workspace",
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
async def get_audit_db_context(
    tenant_id: str,
    uc_uid: str | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Async context manager yielding a tenant-bound audit-role session.

    The audit middleware uses this to write `action_event` rows under the
    `contact_ops_audit` role, enforcing append-only at the DB layer per
    design doc §4.1.9.

    The `action_event` INSERT policy (`ae_audit_insert`) has
    `WITH CHECK (tenant_id = current_tenant_id())`, and `current_tenant_id()`
    reads the `app.tenant_id` GUC. Once the audit role is RLS-subject (the
    Phase 4 cutover), the GUC MUST be set on this session before the INSERT or
    the WITH CHECK rejects the row. We therefore bind tenant context here,
    before yielding, via the shared `bind_session_context` helper. `tenant_id`
    is required for this reason; `uc_uid` is bound too for attribution parity
    with the app session.
    """
    async with audit_session_maker() as session:
        await bind_session_context(session, tenant_id, uc_uid)
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
