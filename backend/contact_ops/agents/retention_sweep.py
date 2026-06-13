"""retention-sweep — daily Celery beat task that ages-out + purges PII.

Two responsibilities, both DORMANT behind config flags (mirrors the
entitlement/billing/garage "present but OFF" posture):

1. **Tombstone grace expiry** — persons that were tombstoned (Art.17 phase 1)
   and whose ``purge_after`` has elapsed are hard-purged (phase 2).
2. **Retention ageing** — persons whose ``retention_class`` TTL has elapsed
   (measured from ``last_interaction_at``) are purged, EXCEPT anything under
   ``legal_hold`` or a non-expiring class (``legal_hold``, ``hipaa_6y``,
   ``indefinite``).

The actual deletion is delegated to the async
``contact_ops.services.compliance_erasure.purge_person`` — the single
purge codepath shared with the Art.17 MCP tool (pre-clears the no-cascade
orphans, anonymizes the action_event ledger in place, lets CASCADE clean
the children, best-effort external store purge). This module NEVER inlines
the delete logic; it only *selects* candidates and *drives* the purge.

Shape copied from ``contact_ops.agents.inbox_snooze_flipper``: a sync
``@shared_task`` (the Celery worker has no asyncpg loop), structlog
telemetry, registered by ``contact_ops.agents.runtime`` in the beat
schedule. Unlike the snooze flipper this is a per-tenant loop, so it must
bind RLS per tenant: it enumerates tenants via the admin/migration DSN
(NOBYPASSRLS runtime role sees zero tenants with no GUC bound — same
constraint + same workaround as ``services/_bootstrap_cli.py``), then for
each tenant opens an app-role async session, binds ``app.tenant_id``, and
runs the purge under RLS.

ASYNC-FROM-SYNC: ``compliance_erasure.purge_person`` and
``bind_session_context`` are async, but the Celery task is sync. We drive
one ``asyncio.run`` over an async worker coroutine that owns the whole
per-tenant loop. (The alternative — one ``asyncio.run`` per tenant — would
re-create the event loop N times; a single run keeps the engines/pools warm
and is the inbox_snooze_flipper-compatible choice: that task does all its
work inside one ``engine.begin()`` block; we do all of ours inside one
``asyncio.run``.)

SHADOW: when ``RETENTION_SWEEP_SHADOW`` is True (default) the sweep logs
``retention_would_purge`` with per-tenant counts and purges NOTHING. Flip
it False (and ``RETENTION_SWEEP_ENABLED`` True) to actually purge.
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


# Retention classes that NEVER age out via the TTL path. ``legal_hold`` is
# also gated by the boolean column, but a row can carry the class without the
# flag (or vice-versa) so both guards are independent. ``indefinite`` is an
# explicit "keep forever" intent. ``hipaa_6y`` is excluded from the generic
# TTL path because its (long) clock is HIPAA-specific and intentionally NOT
# swept by v1 — Aaron's rule: the sweep NEVER touches legal_hold/hipaa.
_NON_EXPIRING_CLASSES = ("legal_hold", "hipaa_6y", "indefinite")


def _admin_async_url(settings: Settings) -> str | None:
    """The migration/admin DSN, normalised to the asyncpg driver.

    Enumerating ALL tenants is a cross-tenant read; the runtime role is
    NOBYPASSRLS so with no GUC bound it sees ZERO tenants. The admin role
    (``MIGRATION_DATABASE_URL``) bypasses RLS — use it for enumeration ONLY
    (never for the purge itself, which must run RLS-bound under the app role).
    Same idiom as ``services/_bootstrap_cli._admin_async_url``.
    """
    raw = settings.MIGRATION_DATABASE_URL
    if not raw:
        return None
    scheme, _, rest = raw.partition("://")
    if not rest:
        return raw
    return f"postgresql+asyncpg://{rest}"


async def _enumerate_tenant_ids(settings: Settings) -> list[str]:
    """Return every tenant id, read via the admin (BYPASSRLS) DSN.

    Returns ``[]`` (and logs a loud warning) when no admin DSN is configured —
    under the runtime role RLS hides all tenants with no GUC bound, so the
    sweep would silently no-op. Failing soft (empty list) is correct: a sweep
    that can't enumerate purges nothing rather than erroring the beat tick.
    """
    admin_url = _admin_async_url(settings)
    if not admin_url:
        logger.warning(
            "retention_sweep_no_admin_dsn",
            reason="MIGRATION_DATABASE_URL unset; runtime role cannot enumerate "
            "tenants under RLS — sweep no-ops",
        )
        return []
    engine = create_async_engine(admin_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT id FROM tenants"))).all()
    finally:
        await engine.dispose()
    return [str(r[0]) for r in rows]


async def _candidates_for_tenant(
    db: AsyncSession, *, settings: Settings, limit: int
) -> list[str]:
    """Select person ids eligible for purge in the RLS-bound tenant.

    Eligibility = phase-2 tombstone grace elapsed
    (``tombstoned_at IS NOT NULL AND purge_after <= now()``) OR retention-class
    TTL elapsed (``last_interaction_at`` older than the class TTL). In BOTH
    cases ``legal_hold`` rows and the non-expiring retention classes are
    excluded — Aaron's invariant: the sweep NEVER touches legal_hold.

    The TTL clock falls back to ``created_at`` when ``last_interaction_at`` is
    NULL (a person we've never interacted with still ages out), and a NULL
    class is treated as the default ``operational_2y``.
    """
    result = await db.execute(
        text(
            """
            SELECT id::text
            FROM persons
            WHERE legal_hold = false
              AND COALESCE(retention_class::text, 'operational_2y')
                  NOT IN ('legal_hold', 'hipaa_6y', 'indefinite')
              AND (
                    -- phase-2: tombstone grace has elapsed
                    (tombstoned_at IS NOT NULL AND purge_after <= now())
                    -- retention ageing by class TTL off last activity
                    OR (
                        COALESCE(last_interaction_at, created_at)
                          < now() - make_interval(
                              days => CASE
                                  COALESCE(retention_class::text, 'operational_2y')
                                  WHEN 'ephemeral_30d' THEN :ephemeral_days
                                  WHEN 'operational_2y' THEN :operational_days
                                  ELSE :operational_days
                              END
                          )
                    )
              )
            ORDER BY purge_after NULLS LAST, last_interaction_at NULLS FIRST
            LIMIT :limit
            """
        ),
        {
            "ephemeral_days": int(settings.RETENTION_EPHEMERAL_TTL_DAYS),
            "operational_days": int(settings.RETENTION_OPERATIONAL_TTL_DAYS),
            "limit": int(limit),
        },
    )
    return [row[0] for row in result.all()]


async def _sweep_async(settings: Settings) -> dict[str, int]:
    """The async worker the sync Celery task drives via ``asyncio.run``.

    Owns the whole per-tenant loop end-to-end so the event loop + the app-role
    engine/pool are created exactly once per tick.
    """
    shadow = bool(settings.RETENTION_SWEEP_SHADOW)
    batch_limit = int(settings.RETENTION_SWEEP_BATCH_LIMIT)

    tenant_ids = await _enumerate_tenant_ids(settings)
    if not tenant_ids:
        return {
            "tenants": 0,
            "candidates": 0,
            "purged": 0,
            "failed": 0,
            "shadow": int(shadow),
        }

    # App-role engine (RLS-subject). The purge MUST run under this role bound
    # to each tenant — NEVER the admin DSN used for enumeration above.
    app_engine = create_async_engine(settings.DATABASE_URL)
    session_maker = async_sessionmaker(
        bind=app_engine, class_=AsyncSession, expire_on_commit=False
    )

    # Lazy import: the erasure service is built in parallel; importing here
    # (not at module top) keeps this module importable for the beat registry
    # even if the service lands a beat later, and matches the "swallow external
    # hiccups" posture — a missing purge codepath disables real purges but the
    # shadow path still reports candidates.
    purge_person = None
    if not shadow:
        try:
            from contact_ops.services.compliance_erasure import (  # noqa: WPS433
                purge_person as _purge_person,
            )

            purge_person = _purge_person
        except Exception:  # noqa: BLE001 — erasure service absent → shadow-only
            logger.warning(
                "retention_sweep_purge_unavailable",
                reason="compliance_erasure.purge_person import failed; "
                "treating this tick as shadow",
            )
            shadow = True

    totals = {
        "tenants": 0,
        "candidates": 0,
        "purged": 0,
        "failed": 0,
        "shadow": int(shadow),
    }

    try:
        for tenant_id in tenant_ids:
            totals["tenants"] += 1
            try:
                async with session_maker() as session:
                    # Bind RLS to this tenant. uc_uid is a service identity —
                    # the sweep is a system actor, not a human session.
                    await bind_session_context(
                        session,
                        tenant_id,
                        "service:retention-sweep",
                        settings,
                    )
                    candidates = await _candidates_for_tenant(
                        session, settings=settings, limit=batch_limit
                    )
                    # Read-only selection — release the txn before purging so
                    # each purge runs in its own committed unit.
                    await session.rollback()

                if not candidates:
                    continue

                totals["candidates"] += len(candidates)

                if shadow:
                    logger.info(
                        "retention_would_purge",
                        tenant_id=tenant_id,
                        count=len(candidates),
                        person_ids=candidates[:50],  # cap log payload
                    )
                    continue

                # Real purge: one RLS-bound session+txn per person so a single
                # failure can't poison the rest of the tenant's batch.
                for person_id in candidates:
                    try:
                        async with session_maker() as session:
                            await bind_session_context(
                                session,
                                tenant_id,
                                "service:retention-sweep",
                                settings,
                            )
                            await purge_person(session, tenant_id, person_id)
                            await session.commit()
                        totals["purged"] += 1
                    except Exception:  # noqa: BLE001 — log + continue the batch
                        totals["failed"] += 1
                        logger.exception(
                            "retention_purge_failed",
                            tenant_id=tenant_id,
                            person_id=person_id,
                        )
            except Exception:  # noqa: BLE001 — one tenant must not abort the sweep
                logger.exception("retention_sweep_tenant_failed", tenant_id=tenant_id)
    finally:
        await app_engine.dispose()

    return totals


@shared_task(  # type: ignore[misc]
    name="contact_ops.agents.tasks.run_retention_sweep",
    bind=True,
    acks_late=True,
)
def run_retention_sweep(self: Any, agent_slug: str = "retention-sweep") -> dict[str, int]:
    """Age-out + purge per retention policy. DORMANT unless explicitly enabled.

    Gated by ``RETENTION_SWEEP_ENABLED`` (default False) — when off, logs
    ``retention_sweep_disabled`` and returns immediately without touching the
    DB. When on it drives ``_sweep_async`` (which honours
    ``RETENTION_SWEEP_SHADOW``, default True → report-only).

    Returns a telemetry dict ``{tenants, candidates, purged, failed, shadow}``.
    """
    settings = get_settings()

    if not settings.RETENTION_SWEEP_ENABLED:
        logger.info("retention_sweep_disabled")
        return {"tenants": 0, "candidates": 0, "purged": 0, "failed": 0, "shadow": 1}

    summary = asyncio.run(_sweep_async(settings))
    logger.info("retention_sweep_complete", **summary)
    return summary


__all__ = ["run_retention_sweep"]
