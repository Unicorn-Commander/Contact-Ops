"""CardDAV HTTP Basic auth via per-device app passwords.

iOS Contacts.app, macOS Contacts.app, and Thunderbird CardBook all
authenticate to CardDAV with HTTP Basic — they cannot mint or refresh
bearer tokens. We never let those clients send the user's real
Keycloak password; instead each device gets a per-device "app password"
generated through the MCP tool surface
(:mod:`contact_ops.mcp.tools.carddav_passwords`), stored only as a
bcrypt hash on :class:`CarddavAppPassword`.

This module is the verification path called by the CardDAV router on
every request. It:

  * Decodes the ``Authorization: Basic`` header
  * Looks up any active app-password row whose username (= ``uc_uid``)
    matches — the RLS policy on ``carddav_app_passwords`` permits this
    cross-tenant SELECT only when the tenant GUC is unset
  * Verifies the bcrypt hash against the supplied password
  * Re-binds the tenant GUC, stamps ``last_used_*``, returns a
    :class:`CarddavPrincipal`
  * Rate-limits failures (5 per minute per (source_ip, username))

The router mounts this auth check OUTSIDE the JWT middleware
(``/carddav`` is added to ``SKIP_PATHS``).
"""

from __future__ import annotations

import base64
import binascii
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import bcrypt
import structlog
from fastapi import HTTPException, Request, status
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from contact_ops.core.config import Settings, get_settings
from contact_ops.core.database import bind_session_context
from contact_ops.models import Tenant
from contact_ops.models.carddav_app_password import CarddavAppPassword

logger = structlog.get_logger(__name__)


# ---------- principal ----------


@dataclass(frozen=True)
class CarddavPrincipal:
    """Resolved identity for a CardDAV request."""

    user_id: str
    tenant_id: uuid.UUID
    tenant_slug: str
    tenant_hipaa: bool
    scopes: tuple[str, ...]
    app_password_id: uuid.UUID


# ---------- engine + sessions ----------


def _build_engine(settings: Settings) -> Any:
    return create_async_engine(
        settings.DATABASE_URL,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=4,
        max_overflow=8,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )


_engine = _build_engine(get_settings())
_session_maker = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@asynccontextmanager
async def open_session() -> AsyncGenerator[AsyncSession, None]:
    """Open an unscoped CardDAV session.

    Caller binds the tenant via :func:`bind_tenant` once the principal
    is resolved. The session commits/rolls back on context exit.
    """

    async with _session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def bind_tenant(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    uc_uid: str | None = None,
) -> None:
    """Set ``app.tenant_id`` (and ``app.uc_uid``) so RLS policies resolve.

    Routes through the central :func:`bind_session_context` helper so the
    tenant and uc_uid GUCs are always set together. CardDAV callers pass the
    principal's ``user_id`` (which IS the uc_uid) as ``uc_uid``.
    """

    await bind_session_context(session, str(tenant_id), uc_uid, get_settings())


# Tests inject a custom opener that yields an in-memory rolled-back session.
SessionOpener = Callable[[], "AsyncIterator[AsyncSession]"]  # noqa: F821
_session_opener_override: Any = None


def install_session_opener(opener: Any) -> None:
    """Tests-only seam: swap the auth session opener.

    ``opener`` must be a callable returning an async context manager
    that yields an :class:`AsyncSession`. The override is consulted
    inside :func:`auth_session`.
    """

    global _session_opener_override
    _session_opener_override = opener


@asynccontextmanager
async def auth_session() -> AsyncGenerator[AsyncSession, None]:
    """Open a session for the auth lookup path (tests can override)."""

    if _session_opener_override is not None:
        async with _session_opener_override() as session:
            yield session
        return
    async with open_session() as session:
        yield session


# ---------- rate limiter ----------


_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_FAILURES = 5

# (source_ip, username) -> [unix_ts, ...] of failed attempts within window.
# Phase 2 stays in-process; Phase 3 swaps for a Redis token bucket once
# the carddav router runs behind multiple uvicorn workers.
_RATE_LIMIT_STATE: dict[tuple[str, str], list[float]] = {}


def _prune_rate_limit(now: float) -> None:
    threshold = now - _RATE_LIMIT_WINDOW_SECONDS
    for key, attempts in list(_RATE_LIMIT_STATE.items()):
        kept = [ts for ts in attempts if ts >= threshold]
        if kept:
            _RATE_LIMIT_STATE[key] = kept
        else:
            del _RATE_LIMIT_STATE[key]


def is_rate_limited(source_ip: str, username: str) -> bool:
    now = time.time()
    _prune_rate_limit(now)
    return len(_RATE_LIMIT_STATE.get((source_ip, username), [])) >= _RATE_LIMIT_MAX_FAILURES


def record_failure(source_ip: str, username: str) -> None:
    now = time.time()
    _prune_rate_limit(now)
    _RATE_LIMIT_STATE.setdefault((source_ip, username), []).append(now)


def record_success(source_ip: str, username: str) -> None:
    _RATE_LIMIT_STATE.pop((source_ip, username), None)


def reset_rate_limits() -> None:
    """Tests-only helper to clear in-process state between cases."""

    _RATE_LIMIT_STATE.clear()


# ---------- header decoding ----------


WWW_AUTHENTICATE_HEADER = 'Basic realm="Contact-Ops CardDAV", charset="UTF-8"'


def parse_basic_auth_header(value: str | None) -> tuple[str, str] | None:
    """Decode an ``Authorization: Basic ...`` header.

    Returns ``(username, password)`` or ``None`` if the header is missing
    or malformed.
    """

    if not value:
        return None
    parts = value.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        raw = base64.b64decode(parts[1].strip(), validate=False).decode(
            "utf-8", errors="strict"
        )
    except (binascii.Error, UnicodeDecodeError):
        return None
    if ":" not in raw:
        return None
    user, _, password = raw.partition(":")
    return user, password


# ---------- the resolve entry point ----------


async def resolve_principal(request: Request) -> CarddavPrincipal:
    """Verify CardDAV Basic auth on a request and return the principal.

    Raises :class:`fastapi.HTTPException` with status 401 when the
    credentials are missing or invalid (plus a ``WWW-Authenticate``
    header so clients reprompt), or 429 when the (ip, username) pair
    is over its failure budget.
    """

    source_ip = _extract_source_ip(request)
    creds = parse_basic_auth_header(request.headers.get("authorization"))
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="credentials required",
            headers={"WWW-Authenticate": WWW_AUTHENTICATE_HEADER},
        )

    username, password = creds
    username = username.strip()
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": WWW_AUTHENTICATE_HEADER},
        )

    if is_rate_limited(source_ip, username):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed authentication attempts; retry later",
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW_SECONDS)},
        )

    async with auth_session() as session:
        row, tenant = await _verify_password_any_tenant(
            session, username=username, password=password
        )
        if row is None or tenant is None:
            record_failure(source_ip, username)
            logger.info(
                "carddav_auth_failed",
                username=username,
                source_ip=source_ip,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
                headers={"WWW-Authenticate": WWW_AUTHENTICATE_HEADER},
            )

        await _stamp_last_used(
            session,
            row=row,
            tenant_id=tenant.id,
            user_agent=request.headers.get("user-agent"),
            source_ip=source_ip,
        )
        record_success(source_ip, username)
        return CarddavPrincipal(
            user_id=username,
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            tenant_hipaa=tenant.hipaa_mode,
            scopes=tuple(row.scopes or ()),
            app_password_id=row.id,
        )


# ---------- internals ----------


async def _verify_password_any_tenant(
    session: AsyncSession,
    *,
    username: str,
    password: str,
) -> tuple[CarddavAppPassword | None, Tenant | None]:
    """Find an active app-password row whose bcrypt matches ``password``.

    The RLS policy on ``carddav_app_passwords`` permits cross-tenant
    SELECT when ``current_tenant_id()`` is NULL — which is exactly the
    state during this initial lookup. After this call, the router sets
    the tenant GUC for the resolved tenant before any further queries.
    """

    stmt = (
        select(CarddavAppPassword, Tenant)
        .join(Tenant, Tenant.id == CarddavAppPassword.tenant_id)
        .where(
            CarddavAppPassword.user_id == username,
            CarddavAppPassword.revoked_at.is_(None),
        )
    )
    rows = (await session.execute(stmt)).all()

    candidate = password.encode("utf-8")
    for app_password, tenant in rows:
        try:
            stored = app_password.password_hash.encode("utf-8")
            if bcrypt.checkpw(candidate, stored):
                return app_password, tenant
        except ValueError:
            # Malformed hash on disk — skip silently; do not leak via timing.
            continue
    return None, None


async def _stamp_last_used(
    session: AsyncSession,
    *,
    row: CarddavAppPassword,
    tenant_id: uuid.UUID,
    user_agent: str | None,
    source_ip: str | None,
) -> None:
    """Bump ``last_used_*`` columns on the matching app-password row.

    Binds the tenant GUC first so the RLS modify policy permits the update.
    """

    await bind_tenant(session, tenant_id, uc_uid=row.user_id)
    await session.execute(
        update(CarddavAppPassword)
        .where(CarddavAppPassword.id == row.id)
        .values(
            last_used_at=text("now()"),
            last_used_user_agent=user_agent,
            last_used_ip=source_ip,
        )
    )


def _extract_source_ip(request: Request) -> str:
    """Pick the source IP for rate-limiting + audit purposes.

    Prefers ``X-Forwarded-For`` left-most when present (set by Traefik
    in front of Contact-Ops), falls back to ``request.client.host``.
    """

    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    client = request.client
    return client.host if client is not None else "unknown"


# ---------- helpers used by MCP tools (not part of the auth fast path) ----------


def hash_app_password(plaintext: str) -> str:
    """Return a bcrypt hash for storage on :class:`CarddavAppPassword`.

    Cost factor 12 is the 2026 baseline — high enough to resist offline
    brute force but cheap enough that an iPhone reauth completes within
    a couple of hundred milliseconds.
    """

    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode(
        "utf-8"
    )


def verify_app_password(plaintext: str, stored_hash: str) -> bool:
    """Constant-time bcrypt compare for testing / tool surfaces."""

    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), stored_hash.encode("utf-8"))
    except ValueError:
        return False
