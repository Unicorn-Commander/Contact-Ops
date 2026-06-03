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
from typing import Any

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


def _resolve_effective_workspace(
    request: Request, claims: dict[str, Any], settings: Settings
) -> tuple[str, bool]:
    """Resolve the effective workspace id for this request + whether it was
    addressed per-request (i.e. came from a path/header rather than the claim).

    Precedence (SUITE-IDENTITY §8 D4 / Spec 3 §4), applied ONLY when
    ``WORKSPACE_PER_REQUEST_ENABLED`` is on:

        path ``…/workspaces/{workspace_id}/…``  ->  ``X-Workspace-Id`` header
        ->  ``workspace_id`` claim  ->  ``tenant_id`` claim

    When the flag is OFF this returns the claim verbatim (``tenant_id``, with the
    ``workspace_id`` synonym the middleware already folded in), so behavior is
    byte-identical to before: the path/header are NOT consulted at all.

    The returned id is DATA, never the authz boundary — the caller MUST pass it
    through the membership gate before binding it to ``app.tenant_id``.
    """
    claim_ws = str(claims.get("tenant_id") or claims.get("workspace_id") or "")
    if not settings.WORKSPACE_PER_REQUEST_ENABLED:
        return claim_ws, False

    # (1) path segment …/workspaces/{id}/…  (most explicit)
    path_ws = _workspace_id_from_path(request.url.path)
    if path_ws:
        return path_ws, (path_ws != claim_ws)

    # (2) X-Workspace-Id header
    header_ws = (request.headers.get("x-workspace-id") or "").strip()
    if header_ws:
        return header_ws, (header_ws != claim_ws)

    # (3)/(4) workspace_id claim -> tenant_id claim (already folded together)
    return claim_ws, False


def _workspace_id_from_path(path: str) -> str:
    """Extract ``{workspace_id}`` from a ``…/workspaces/{workspace_id}/…`` path.

    Returns the segment immediately after a ``workspaces`` segment, or ``""`` if
    the path is not workspace-addressed. Validates it parses as a UUID so a
    non-UUID path segment can never reach the GUC bind (it falls through to the
    claim instead).
    """
    import uuid as _uuid

    parts = [p for p in path.split("/") if p]
    for i, seg in enumerate(parts[:-1]):
        if seg == "workspaces":
            candidate = parts[i + 1]
            try:
                _uuid.UUID(candidate)
            except ValueError:
                return ""
            return candidate
    return ""


async def get_tenant_db(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a session pre-bound to the caller's tenant.

    Reads `tenant_id` from `request.state.jwt_claims` (set by
    JWTValidationMiddleware). Issues `SET LOCAL app.tenant_id = ?` before
    yielding, so RLS policies on `persons`, `organizations`, etc. resolve
    the caller's tenant context. The GUC name is ALWAYS `app.tenant_id`
    (SUITE-IDENTITY §6): only the *wire claim* is `workspace_id`.

    Per-request addressing (SUITE-IDENTITY §8 D4), behind
    ``WORKSPACE_PER_REQUEST_ENABLED`` (default OFF = the KC-attribute/claim
    behavior, byte-identical to before): when ON, the effective workspace is
    resolved via the precedence path/header/claim and the membership gate is
    RE-RUN against the RESOLVED workspace before binding — the path/header is
    data, never the authz boundary. A path/header targeting a workspace the
    caller is not an active member of yields 403 BEFORE any GUC bind, even if
    MEMBERSHIP_GATE_ENFORCED is off for the claim path (per-request addressing
    that is not membership-checked would be a horizontal-access hole, so the
    re-gate on a per-request override is unconditional for KC sessions).

    Raises 401 if no JWT claims are present on the request — the JWT
    middleware should have already rejected unauthenticated requests, so
    reaching here without claims is a programming error, but we fail
    closed regardless.
    """
    claims = getattr(request.state, "jwt_claims", None)
    if not claims or not (claims.get("tenant_id") or claims.get("workspace_id")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="tenant context unresolved",
        )
    tenant_id, per_request_override = _resolve_effective_workspace(
        request, claims, settings
    )
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="tenant context unresolved",
        )
    # uc_uid is normalized to a non-empty value in JWTValidationMiddleware
    # (falls back to `sub`); read it from the same claims source as tenant_id.
    uc_uid = claims.get("uc_uid") or claims.get("sub")
    is_kc_session = claims.get("iss") == settings.KEYCLOAK_ISSUER

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
        #
        # When the workspace was addressed PER-REQUEST (path/header differs from
        # the token's own workspace), the re-gate is UNCONDITIONAL for a KC
        # session regardless of MEMBERSHIP_GATE_ENFORCED: an override the user is
        # not a member of must never bind, or X-Workspace-Id/path would be a
        # cross-workspace access hole. The token's own claim path keeps the
        # existing flag-gated behavior.
        must_gate = is_kc_session and (
            settings.MEMBERSHIP_GATE_ENFORCED or per_request_override
        )
        if must_gate and not await _user_is_active_member(
            session, uc_uid, tenant_id
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
