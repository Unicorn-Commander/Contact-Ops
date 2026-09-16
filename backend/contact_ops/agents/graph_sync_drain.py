"""graph-sync-drain — Celery beat task that drains the graph-sync outboxes.

The GraphSyncWorker (``contact_ops.agents.graph_sync``) has always known how to
write into each tenant's FalkorDB graph — it just had no runner. Two sources feed
those graphs and both accumulated forever without one:

* ``event_outbox`` edge channels (``falkordb.edge.create`` from the dedup merge
  tools, ``graph.sync_edge`` from the voice-match resolver) — the rows that
  actually pile up on this branch. ``drain_edge_events`` applies them.
* ``graph_sync_outbox`` — current-affiliation triggers queue source references;
  older payload rows retain their original drain path.
  ``drain_pending_outbox`` claims it and no-ops when empty.

This module is the runner for both: a sync ``@shared_task`` wired into the beat
schedule (``contact_ops.agents.runtime``) so the outboxes actually drain.

CROSS-TENANT UNDER THE ADMIN DSN: both outboxes are RLS-protected, and the drain
is inherently cross-tenant (it claims whatever is pending and each row carries its
own ``tenant_id``). The runtime role is NOBYPASSRLS so with no GUC bound it sees
ZERO rows — so the drain runs under the admin/migration DSN
(``MIGRATION_DATABASE_URL``, BYPASSRLS), exactly like the global calibration pass.
The FalkorDB write still lands in the correct per-tenant graph because each row's
target graph is resolved from its ``tenant_id`` (event_outbox) or inline
``graph_name`` (graph_sync_outbox). Shape mirrors
``contact_ops.agents.retention_sweep``: enumerate/act under the admin DSN, one
``asyncio.run`` per tick, structlog telemetry.

ASYNC-FROM-SYNC: the Celery worker has no asyncpg loop, so the task is sync and
drives one ``asyncio.run`` over the async drain coroutine.

CHEAP WHEN EMPTY: ``FalkorDBGraphClient`` connects lazily (first query only), so
a tick over an empty outbox claims zero rows and never touches FalkorDB. That is
why this task defaults ENABLED (``GRAPH_SYNC_DRAIN_ENABLED``) — unlike the
destructive dedup/retention/calibration tasks — an idle drain costs one indexed
``SELECT ... LIMIT`` per minute.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from contact_ops.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)


def _admin_async_url(settings: Settings) -> str | None:
    """The migration/admin DSN, normalised to the asyncpg driver.

    ``graph_sync_outbox`` is RLS-protected; the drain is cross-tenant and the
    runtime role (NOBYPASSRLS) sees zero rows with no GUC bound. Use the admin
    role (``MIGRATION_DATABASE_URL``, BYPASSRLS) so the claim sees every pending
    row — each row carries its own tenant_id + graph_name, so the FalkorDB write
    still targets the right graph. Same idiom as ``agent_tasks._admin_async_url``.
    """
    raw = settings.MIGRATION_DATABASE_URL
    if not raw:
        return None
    _scheme, _, rest = raw.partition("://")
    if not rest:
        return raw
    return f"postgresql+asyncpg://{rest}"


async def _drain_async(settings: Settings) -> dict[str, int]:
    """The async worker the sync Celery task drives via ``asyncio.run``.

    Opens one admin-DSN engine for the tick, drains the outbox, disposes. Returns
    ``{drained}`` (rows successfully written to FalkorDB this tick).
    """
    admin_url = _admin_async_url(settings)
    if not admin_url:
        logger.warning(
            "graph_sync_drain_no_admin_dsn",
            reason="MIGRATION_DATABASE_URL unset; runtime role cannot see "
            "graph_sync_outbox rows under RLS — drain no-ops",
        )
        return {"drained": 0}

    # Lazy import: the graph_sync package pulls the FalkorDB client (redis). Keep
    # it out of the module top so the beat scheduler can import this module to
    # register the task without those deps loading at schedule-build time.
    from contact_ops.agents.graph_sync import drain_edge_events, drain_pending_outbox
    from contact_ops.agents.graph_sync.current_projection import drain_current_projection

    engine = create_async_engine(admin_url)
    sessions = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with sessions() as db:
            batch = int(settings.GRAPH_SYNC_DRAIN_BATCH_LIMIT)
            # Reference-only current projection, legacy graph payloads, and
            # the real accumulating source: the ``event_outbox`` edge channels the
            # dedup + voice-match tools publish to.
            current = await drain_current_projection(db=db, batch_size=batch)
            drained = await drain_pending_outbox(db=db, batch_size=batch)
            edges = await drain_edge_events(db=db, batch_size=batch)
    finally:
        await engine.dispose()

    return {"drained": int(drained), "edges": int(edges), "current": int(current)}


@shared_task(  # type: ignore[misc]
    name="contact_ops.agents.tasks.run_graph_sync",
    bind=True,
    acks_late=True,
)
def run_graph_sync(self: Any, agent_slug: str = "graph-sync-worker") -> dict[str, int]:
    """Drain ``graph_sync_outbox`` into per-tenant FalkorDB graphs.

    Enabled by default (``GRAPH_SYNC_DRAIN_ENABLED``): the drain is
    non-destructive and a no-op when the outbox is empty. With the flag False it
    logs ``graph_sync_drain_disabled`` and touches nothing. Returns ``{drained}``.
    """
    settings = get_settings()

    if not settings.GRAPH_SYNC_DRAIN_ENABLED:
        logger.info("graph_sync_drain_disabled")
        return {"drained": 0}

    summary = asyncio.run(_drain_async(settings))
    if summary.get("drained") or summary.get("edges"):
        logger.info("graph_sync_drain_complete", **summary)
    return summary


__all__ = ["run_graph_sync", "_drain_async"]
