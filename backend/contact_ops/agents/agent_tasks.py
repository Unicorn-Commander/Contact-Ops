"""Agent-execution bridge: run a BATCH agent through ``BaseAgent.execute``.

This is the foundation the whole agent fleet was missing. The governance
machinery (circuit breaker, workspace kill-switch, trust ladder, action_event,
cost guard) was built and tested, but nothing ran an agent through it in
production: there was not a single ``AgentContext`` construction site outside
tests. This module is that site.

WHAT IT DOES: for a given BATCH agent slug, it loops over every tenant and runs
the agent once per tenant under that tenant's RLS, exactly the way the MCP
server builds a request context (``mcp/server.py``): an app-role session + an
audit-role session, both bound to the tenant, fed to ``BaseAgent.execute``.

PER-TENANT UNDER RLS (mirrors ``retention_sweep`` / ``inbox_snooze_flipper``):
the Celery worker connects as ``contact_ops_runtime`` (NOBYPASSRLS), which sees
zero tenants with no GUC bound, so we enumerate tenants via the admin/migration
DSN (``MIGRATION_DATABASE_URL``, BYPASSRLS, enumeration ONLY) then run each
tenant bound under the app/audit roles.

ASYNC-FROM-SYNC: the Celery task is sync (no asyncpg loop in the worker); it
drives one ``asyncio.run`` over an async coroutine that owns the whole
per-tenant loop, so the engines/pools are created once per tick.

AUDIT GUC PERSISTENCE: ``BaseAgent.propose_action`` writes ``action_event`` via
the audit session and COMMITs once per proposal. The ``ae_audit_insert`` policy
is ``WITH CHECK (tenant_id = current_tenant_id())`` and ``SET LOCAL`` is cleared
by COMMIT, so a multi-proposal agent (Dedup emits one event per merge) would
have its second-and-later inserts rejected once the GUC is gone. We attach an
``after_begin`` listener to the audit session that re-applies the tenant GUC on
every new transaction. This is leak-safe: COMMIT still clears the SET LOCAL, so
a pooled connection is never returned carrying a tenant binding.

HEAVY DEPS STAY LAZY: the agent class is imported inside the task (Dedup pulls
pandas), never at module top, so the slim API image can force-import this module
(via ``runtime.make_celery_app``) without the batch-only deps.

DORMANT BY DEFAULT: ``run_dedup`` is gated by ``DEDUP_AGENT_ENABLED`` (default
False) so deploying this wiring runs nothing until an operator flips the flag.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable
from uuid import UUID

import structlog
from celery import shared_task
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from contact_ops.agents.base import AgentContext, BaseAgent, Visibility
from contact_ops.agents.circuit_breaker import CircuitBreaker
from contact_ops.agents.cost_guard import CostBudget, CostGuard
from contact_ops.core.config import Settings, get_settings
from contact_ops.core.database import bind_session_context

logger = structlog.get_logger(__name__)


# A factory takes a slug and returns a ready-to-run BaseAgent instance. Default
# resolution lazy-imports the concrete class (heavy deps live in the worker);
# tests inject a factory returning a lightweight agent.
AgentFactory = Callable[[str], BaseAgent]


def _admin_async_url(settings: Settings) -> str | None:
    """The migration/admin DSN, normalised to the asyncpg driver.

    Enumerating ALL tenants is a cross-tenant read; the runtime role is
    NOBYPASSRLS so with no GUC bound it sees ZERO tenants. The admin role
    (``MIGRATION_DATABASE_URL``) bypasses RLS, use it for enumeration ONLY
    (never for the agent run, which executes RLS-bound under the app role).
    Same idiom as ``retention_sweep._admin_async_url``.
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

    Returns ``[]`` (and logs a loud warning) when no admin DSN is configured:
    under the runtime role RLS hides all tenants with no GUC bound, so the run
    would silently no-op. Failing soft (empty list) is correct, a tick that
    can't enumerate runs nothing rather than erroring the beat.
    """
    admin_url = _admin_async_url(settings)
    if not admin_url:
        logger.warning(
            "agent_bridge_no_admin_dsn",
            reason="MIGRATION_DATABASE_URL unset; runtime role cannot enumerate "
            "tenants under RLS, agent run no-ops",
        )
        return []
    engine = create_async_engine(admin_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT id FROM tenants"))).all()
    finally:
        await engine.dispose()
    return [str(r[0]) for r in rows]


def _keep_tenant_bound(
    session: AsyncSession, tenant_id: str, settings: Settings
) -> None:
    """Re-apply the tenant GUC on every transaction ``session`` begins.

    ``propose_action`` commits the audit session once per proposal; COMMIT
    clears ``SET LOCAL``, so without this the next ``action_event`` insert would
    fail the ``ae_audit_insert`` check ``tenant_id = current_tenant_id()``. The
    after_begin hook re-binds at the start of each transaction. Leak-safe: the
    binding is still transaction-local (cleared by COMMIT), so a pooled
    connection is never returned carrying a tenant id.
    """
    guc = settings.TENANT_GUC_NAME

    @event.listens_for(session.sync_session, "after_begin")
    def _reapply(_sess: Any, _trans: Any, connection: Any) -> None:
        connection.execute(
            text("SELECT set_config(:k, :v, true)"),
            {"k": guc, "v": str(tenant_id)},
        )


def _default_agent_factory(slug: str) -> BaseAgent:
    """Resolve a BATCH agent slug to a fresh instance, lazy-importing the class.

    The heavy import (Dedup -> pandas) happens HERE, inside the worker task, so
    this module stays importable in the slim API image that force-imports it via
    ``runtime.make_celery_app``.
    """
    if slug == "dedup":
        from contact_ops.agents.dedup.agent import DedupAgent

        return DedupAgent()
    raise ValueError(f"no batch-agent runner registered for slug {slug!r}")


async def _run_agent_all_tenants(
    agent_slug: str,
    settings: Settings,
    *,
    agent_factory: AgentFactory | None = None,
    redis_client: Any | None = None,
) -> dict[str, int]:
    """Run ``agent_slug`` once per tenant through ``BaseAgent.execute``.

    Owns the whole per-tenant loop so the app/audit engines are created once per
    tick. Each tenant gets its own app-role + audit-role session bound to it
    (exactly like ``mcp/server.py`` builds a request context). A single tenant's
    failure is logged and skipped so it cannot abort the rest of the fleet run.

    ``redis_client`` is injectable for tests (a fakeredis client); in production
    it is None and a real client is built from ``settings.REDIS_URL`` and closed
    at the end. The circuit breaker is fail-closed: if redis is unreachable, the
    breaker check raises and that tenant is counted failed (no agent runs without
    a consultable breaker), which is the safe posture.

    Returns telemetry ``{tenants, ran, failed, proposals}``.
    """
    factory = agent_factory or _default_agent_factory

    tenant_ids = await _enumerate_tenant_ids(settings)
    if not tenant_ids:
        return {"tenants": 0, "ran": 0, "failed": 0, "proposals": 0}

    # Resolve the agent class once (a failed heavy import should fail loudly
    # before we touch any tenant, not once per tenant).
    agent = factory(agent_slug)

    # Dedicated engines for this tick. The agent runs RLS-bound under the app
    # role; action_event is written under the audit role. Both NEVER use the
    # admin DSN used for enumeration above.
    app_engine = create_async_engine(settings.DATABASE_URL)
    audit_engine = create_async_engine(
        settings.AUDIT_DATABASE_URL or settings.DATABASE_URL
    )
    app_sessions = async_sessionmaker(
        bind=app_engine, class_=AsyncSession, expire_on_commit=False
    )
    audit_sessions = async_sessionmaker(
        bind=audit_engine, class_=AsyncSession, expire_on_commit=False
    )

    totals = {"tenants": 0, "ran": 0, "failed": 0, "proposals": 0}
    # Each agent runs as a system actor, not a human session.
    actor = f"service:{agent_slug}"

    # Build a redis client only when one was not injected; close only what we
    # built so a test-supplied fakeredis is left to the caller.
    owns_redis = redis_client is None
    redis = redis_client
    try:
        if redis is None:
            from redis.asyncio import Redis

            redis = Redis.from_url(settings.REDIS_URL)

        for tenant_id in tenant_ids:
            totals["tenants"] += 1
            try:
                async with app_sessions() as db, audit_sessions() as audit_db:
                    await bind_session_context(db, tenant_id, actor, settings)
                    await bind_session_context(audit_db, tenant_id, actor, settings)
                    # The audit session is committed once per proposal; keep it
                    # tenant-bound across those commits.
                    _keep_tenant_bound(audit_db, tenant_id, settings)

                    ctx = AgentContext(
                        db=db,
                        audit_db=audit_db,
                        tenant_id=UUID(tenant_id),
                        visibility=Visibility.PRIVATE,
                        triggered_by=actor,
                        event_payload={},
                        cost_guard=CostGuard(db=db, budget=CostBudget()),
                        circuit_breaker=CircuitBreaker(redis=redis, db=db),
                    )
                    result = await agent.execute(ctx=ctx)
                    # Flush the app-side reads/writes for this tenant; the audit
                    # ledger was already committed per proposal inside execute.
                    await db.commit()

                totals["ran"] += 1
                totals["proposals"] += int(result.proposals_emitted)
            except Exception:  # noqa: BLE001 one tenant must not abort the run
                totals["failed"] += 1
                logger.exception(
                    "agent_bridge_tenant_failed",
                    agent_slug=agent_slug,
                    tenant_id=tenant_id,
                )
    finally:
        if owns_redis and redis is not None:
            await redis.aclose()
        await app_engine.dispose()
        await audit_engine.dispose()

    return totals


@shared_task(  # type: ignore[misc]
    name="contact_ops.agents.tasks.run_dedup",
    bind=True,
    acks_late=True,
)
def run_dedup(self: Any, agent_slug: str = "dedup") -> dict[str, int]:
    """Run the Dedup agent across every tenant. DORMANT unless enabled.

    Gated by ``DEDUP_AGENT_ENABLED`` (default False): when off it logs
    ``dedup_agent_disabled`` and returns immediately without enumerating or
    executing. When on it drives ``_run_agent_all_tenants("dedup")``, which runs
    ``DedupAgent.execute`` per tenant under RLS through the full governance path
    (circuit breaker, workspace kill-switch, trust ladder, action_event).

    Returns telemetry ``{tenants, ran, failed, proposals}``.
    """
    settings = get_settings()

    if not settings.DEDUP_AGENT_ENABLED:
        logger.info("dedup_agent_disabled")
        return {"tenants": 0, "ran": 0, "failed": 0, "proposals": 0}

    summary = asyncio.run(_run_agent_all_tenants("dedup", settings))
    logger.info("dedup_agent_complete", **summary)
    return summary


__all__ = ["run_dedup", "_run_agent_all_tenants"]
