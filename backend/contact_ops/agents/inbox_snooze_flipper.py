"""inbox-snooze-flipper -- Celery beat task that re-surfaces snoozed proposals.

Runs every 5 minutes per Aaron's B5. Clears ``snoozed_until`` on
``action_event`` rows whose snooze has elapsed, so the inbox query starts
showing them again. Idempotent + cheap (indexed on ``ae_snoozed_until_idx``).

PER-TENANT UNDER RLS (shape mirrored from
``contact_ops.agents.retention_sweep``): the Celery worker connects as the
``contact_ops_runtime`` role, which is NOBYPASSRLS, so with no
``app.tenant_id`` bound it sees ZERO action_event rows and the UPDATE flips
nothing (the ``ae_modify`` policy is ``USING (tenant_id = current_tenant_id())``
and ``current_tenant_id()`` is NULL with no GUC). So the task enumerates every
tenant via the admin/migration DSN (``MIGRATION_DATABASE_URL`` -- BYPASSRLS,
enumeration ONLY), then for each tenant opens an app-role async session
(``DATABASE_URL``), binds ``app.tenant_id``, and runs the snooze-clearing
UPDATE under RLS.

ASYNC-FROM-SYNC: the Celery task is sync (no asyncpg loop in the worker), so it
drives one ``asyncio.run`` over an async coroutine that owns the whole
per-tenant loop -- the same idiom as ``retention_sweep`` (the event loop + the
app-role engine/pool are created exactly once per tick).

The two enumeration helpers (``_admin_async_url`` / ``_enumerate_tenant_ids``)
are mirrored locally from ``retention_sweep`` rather than imported: those are
module-private there, and duplicating ~25 trivial lines keeps this operational
task self-contained and avoids importing (and so registering) the retention
task as a side effect of importing this one.

Registered by ``contact_ops.agents.runtime`` as part of the beat schedule. The
task name is the standard ``contact_ops.agents.tasks.run_*`` prefix.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from celery import shared_task
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from contact_ops.core.config import Settings, get_settings
from contact_ops.core.database import bind_session_context

logger = structlog.get_logger(__name__)


def _admin_async_url(settings: Settings) -> str | None:
    """The migration/admin DSN, normalised to the asyncpg driver.

    Enumerating ALL tenants is a cross-tenant read; the runtime role is
    NOBYPASSRLS so with no GUC bound it sees ZERO tenants. The admin role
    (``MIGRATION_DATABASE_URL``) bypasses RLS -- use it for enumeration ONLY
    (never for the flip itself, which must run RLS-bound under the app role).
    Mirrors ``retention_sweep._admin_async_url``.
    """
    raw = settings.MIGRATION_DATABASE_URL
    if not raw:
        return None
    _scheme, _, rest = raw.partition("://")
    if not rest:
        return raw
    return f"postgresql+asyncpg://{rest}"


async def _enumerate_tenant_ids(settings: Settings) -> list[str]:
    """Return every tenant id, read via the admin (BYPASSRLS) DSN.

    Returns ``[]`` (and logs a loud warning) when no admin DSN is configured --
    under the runtime role RLS hides all tenants with no GUC bound, so the flip
    would silently no-op. Failing soft (empty list) is correct: a tick that
    can't enumerate flips nothing rather than erroring the beat. Mirrors
    ``retention_sweep._enumerate_tenant_ids``.
    """
    admin_url = _admin_async_url(settings)
    if not admin_url:
        logger.warning(
            "inbox_snooze_flipper_no_admin_dsn",
            reason="MIGRATION_DATABASE_URL unset; runtime role cannot enumerate "
            "tenants under RLS -- flip no-ops",
        )
        return []
    engine = create_async_engine(admin_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT id FROM tenants"))).all()
    finally:
        await engine.dispose()
    return [str(r[0]) for r in rows]


async def _flip_async(settings: Settings) -> dict[str, int]:
    """Clear expired snoozes across every tenant, RLS-bound per tenant.

    The async worker the sync Celery task drives via ``asyncio.run``. Owns the
    whole per-tenant loop end-to-end so the event loop + the app-role
    engine/pool are created exactly once per tick.

    Each tenant's flip is its own committed unit (one bound session + txn), so a
    single tenant's failure logs and is skipped without aborting the tick. The
    returned ``flipped`` count is summed across tenants.
    """
    tenant_ids = await _enumerate_tenant_ids(settings)
    if not tenant_ids:
        return {"flipped": 0}

    # App-role engine (RLS-subject). The flip MUST run under this role bound to
    # each tenant -- NEVER the admin DSN used for enumeration above.
    app_engine = create_async_engine(settings.DATABASE_URL)
    session_maker = async_sessionmaker(
        bind=app_engine, class_=AsyncSession, expire_on_commit=False
    )

    flipped = 0
    try:
        for tenant_id in tenant_ids:
            try:
                async with session_maker() as session:
                    # Bind RLS to this tenant. The flipper is a system actor, so
                    # uc_uid is a ``service:`` identity, not a human session.
                    await bind_session_context(
                        session,
                        tenant_id,
                        "service:inbox-snooze-flipper",
                        settings,
                    )
                    result = await session.execute(
                        text(
                            """
                            UPDATE action_event
                            SET snoozed_until = NULL
                            WHERE status = 'proposed'
                              AND snoozed_until IS NOT NULL
                              AND snoozed_until <= now()
                            """
                        )
                    )
                    await session.commit()
                    flipped += result.rowcount or 0
            except Exception:  # noqa: BLE001 -- one tenant must not abort the tick
                logger.exception(
                    "inbox_snooze_flipper_tenant_failed", tenant_id=tenant_id
                )
    finally:
        await app_engine.dispose()

    return {"flipped": flipped}


@shared_task(  # type: ignore[misc]
    name="contact_ops.agents.tasks.run_inbox_snooze_flipper",
    bind=True,
    acks_late=True,
)
def flip_expired_snoozes(
    self: Any, agent_slug: str = "inbox-snooze-flipper"
) -> dict[str, int]:
    """Clear ``snoozed_until`` for every action_event whose snooze expired.

    Enumerates tenants via the admin DSN then flips RLS-bound under the app role
    per tenant (the runtime role is NOBYPASSRLS, so an unbound flip would match
    zero rows). Returns ``{"flipped": <count>}`` summed across tenants for
    observability. Idempotent. The Calibration Daemon doesn't read this -- it's
    purely operational telemetry.
    """
    settings = get_settings()
    summary = asyncio.run(_flip_async(settings))
    if summary["flipped"] > 0:
        logger.info("inbox_snooze_flipper.flipped", count=summary["flipped"])
    return summary


__all__ = ["flip_expired_snoozes"]
