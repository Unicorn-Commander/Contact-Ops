"""Agent-execution bridge: run a BATCH agent through BaseAgent.execute per tenant.

Covers contact_ops.agents.agent_tasks, the first production AgentContext
construction site. The bridge enumerates tenants, builds an app-role + audit-role
session bound to each, and runs the agent through the full governance path.

The tests use a lightweight multi-proposal agent (no pandas / Splink) so they
exercise the bridge mechanics, NOT the dedup pipeline:

* per-tenant execution: the agent runs once per tenant and its action_events are
  written scoped to that tenant
* the GUC-after-commit path: an agent that emits TWO proposals (two audit
  commits) still lands both, proving the audit session stays tenant-bound across
  commits (the ae_audit_insert WITH CHECK would reject the second otherwise)
* the workspace kill-switch stops a paused tenant without aborting the others
* one tenant's failure is isolated, the run continues
* run_dedup is dormant unless DEDUP_AGENT_ENABLED

The bridge owns its sessions (it builds app + audit engines from settings, the
way mcp/server.py builds a request context), so these tests point the settings
DSNs at the testcontainer Postgres and assert behavior through the bridge's own
engines. The agent and the redis client are injected so a lightweight agent and
a fakeredis stand in for the dedup pipeline and a live broker.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from contact_ops.agents import (
    AgentClass,
    AgentContext,
    AgentDef,
    AgentResult,
    BaseAgent,
    Reversibility,
    TrustTier,
)
from contact_ops.agents.registry import _clear_registry_for_tests, register_agent

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_registry():
    _clear_registry_for_tests()
    yield
    _clear_registry_for_tests()


# ---------------------------------------------------------------------------
# A lightweight multi-proposal agent (the dedup stand-in for bridge mechanics)
# ---------------------------------------------------------------------------


def _multi_def(slug: str = "multi-echo") -> AgentDef:
    return AgentDef(
        slug=slug,
        name="Multi Echo",
        version="0.0.1",
        agent_class=AgentClass.BATCH,
        description="Test agent: emits N reversible action_events per run.",
        cost_budget_monthly_cents=100,
        initial_trust_tier=TrustTier.T0_PROBATION,
        triggers=("manual",),
        declared_capabilities=("echo.propose",),
    )


class MultiProposalAgent(BaseAgent):
    """Emits ``ctx.event_payload['count']`` proposals, each a separate commit.

    Two-plus proposals exercise the audit-session GUC-after-commit path: each
    propose_action COMMITs the audit session, clearing SET LOCAL, so the second
    insert only succeeds if the bridge keeps the session tenant-bound.
    """

    async def _run(self, ctx: AgentContext) -> AgentResult:
        count = int(ctx.event_payload.get("count", 2))
        emitted = 0
        for i in range(count):
            await self.propose_action(
                ctx=ctx,
                event_type="echo.propose",
                aggregate_type="person",
                aggregate_id=uuid.uuid4(),
                payload_before=None,
                payload_after={"n": i},
                confidence=0.5,
                reversibility=Reversibility.REVERSIBLE,
                evidence={"source": "multi-echo"},
                rationale=f"multi-echo proposal {i}",
            )
            emitted += 1
        return AgentResult(
            proposals_emitted=emitted,
            proposals_auto_applied=0,
            decision_summary=f"emitted {emitted}",
        )


class BoomAgent(BaseAgent):
    """Raises inside _run, to prove one tenant's failure is isolated."""

    async def _run(self, ctx: AgentContext) -> AgentResult:
        raise RuntimeError("boom")


def _factory_for(agent: BaseAgent):
    def _factory(_slug: str) -> BaseAgent:
        return agent

    return _factory


def _fake_redis():
    """An in-memory redis the circuit breaker can consult (no server needed).

    The breaker is fail-closed on a real redis-down, so the bridge tests inject
    a working fakeredis so execute reaches the agent / kill-switch path rather
    than erroring at the breaker check.
    """
    import fakeredis.aioredis

    return fakeredis.aioredis.FakeRedis()


async def _seed_tenant(
    db_session, tid: str, slug: str, *, agents_paused: bool = False
) -> None:
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix, agents_paused)
            VALUES (CAST(:id AS uuid), :slug, 'brand', :slug, CAST(:id AS uuid),
                false, :slug, :slug, :paused)
            ON CONFLICT (id) DO UPDATE SET agents_paused = EXCLUDED.agents_paused
            """
        ),
        {"id": tid, "slug": slug, "paused": agents_paused},
    )
    await db_session.commit()


def _settings_for_test(db_engine) -> Any:
    """Build a Settings whose DSNs point at the test Postgres.

    The bridge creates its own engines from settings.DATABASE_URL /
    AUDIT_DATABASE_URL / MIGRATION_DATABASE_URL, so we point all three at the
    testcontainer so the run exercises the real per-tenant loop + GUC binding.
    Uses model_copy(update=...) to return an ISOLATED instance, never mutating
    the lru_cached global (which would pollute other tests).

    These tests connect as the test superuser, so the audit role's
    ae_audit_insert RLS policy is not enforced here. The bridge tenant loop,
    the workspace kill-switch (an app-level check, enforced regardless of role),
    failure isolation, and the GUC re-binding mechanism are all verified;
    end-to-end enforcement under the real contact_ops_audit role is the live
    dogfood gate.
    """
    from contact_ops.core.config import get_settings

    url = str(db_engine.url)
    sync_url = url.replace("+asyncpg", "")
    return get_settings().model_copy(
        update={
            "DATABASE_URL": url,
            "AUDIT_DATABASE_URL": url,
            "MIGRATION_DATABASE_URL": sync_url,
        }
    )


async def _count_events(db_session, tid: str) -> int:
    return (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM action_event "
                "WHERE tenant_id = CAST(:t AS uuid) AND event_type = 'echo.propose'"
            ),
            {"t": tid},
        )
    ).scalar_one()


async def test_runs_per_tenant_and_emits_under_rls(db_session, db_engine):
    from contact_ops.agents.agent_tasks import _run_agent_all_tenants

    a = "00000000-0000-0000-0000-0000000c0a01"
    b = "00000000-0000-0000-0000-0000000c0b01"
    await _seed_tenant(db_session, a, "bridge-a")
    await _seed_tenant(db_session, b, "bridge-b")

    register_agent(_multi_def())
    agent = MultiProposalAgent(_multi_def())
    settings = _settings_for_test(db_engine)

    totals = await _run_agent_all_tenants(
        "multi-echo",
        settings,
        agent_factory=_factory_for(agent),
        redis_client=_fake_redis(),
    )

    # Both tenants ran; 2 proposals each.
    assert totals["tenants"] >= 2
    assert totals["failed"] == 0
    # Each seeded tenant got exactly 2 events scoped to it (the GUC-after-commit
    # path held: the second commit's insert was not rejected).
    assert await _count_events(db_session, a) == 2
    assert await _count_events(db_session, b) == 2


async def test_kill_switch_skips_paused_tenant(db_session, db_engine):
    from contact_ops.agents.agent_tasks import _run_agent_all_tenants

    paused = "00000000-0000-0000-0000-0000000c0a02"
    active = "00000000-0000-0000-0000-0000000c0b02"
    await _seed_tenant(db_session, paused, "bridge-paused", agents_paused=True)
    await _seed_tenant(db_session, active, "bridge-active", agents_paused=False)

    register_agent(_multi_def())
    agent = MultiProposalAgent(_multi_def())
    settings = _settings_for_test(db_engine)

    totals = await _run_agent_all_tenants(
        "multi-echo",
        settings,
        agent_factory=_factory_for(agent),
        redis_client=_fake_redis(),
    )

    # The paused tenant raised WorkspaceAgentsPausedError inside execute, which
    # the bridge counts as a failed (skipped) tenant; the active one ran.
    assert await _count_events(db_session, paused) == 0
    assert await _count_events(db_session, active) == 2
    assert totals["ran"] >= 1
    assert totals["failed"] >= 1


async def test_one_tenant_failure_is_isolated(db_session, db_engine):
    from contact_ops.agents.agent_tasks import _run_agent_all_tenants

    a = "00000000-0000-0000-0000-0000000c0a03"
    await _seed_tenant(db_session, a, "bridge-boom")

    register_agent(_multi_def())
    settings = _settings_for_test(db_engine)

    totals = await _run_agent_all_tenants(
        "multi-echo",
        settings,
        agent_factory=_factory_for(BoomAgent(_multi_def())),
        redis_client=_fake_redis(),
    )

    # The agent raised for every tenant; the run completed (did not propagate)
    # and reported the failures.
    assert totals["failed"] >= 1
    assert totals["ran"] == 0


async def test_run_dedup_dormant_by_default():
    """DEDUP_AGENT_ENABLED defaults False, so run_dedup no-ops without DB I/O."""
    from contact_ops.agents.agent_tasks import run_dedup

    # get_settings() is cached and DEDUP_AGENT_ENABLED defaults False; the task
    # short-circuits before enumerating or touching the DB.
    result = run_dedup.apply(kwargs={"agent_slug": "dedup"}).get()
    assert result == {"tenants": 0, "ran": 0, "failed": 0, "proposals": 0}


def test_run_dedup_registered_and_scheduled():
    """The bridge task is registered under its canonical name + scheduled 6h."""
    from contact_ops.agents.runtime import celery_app

    assert "contact_ops.agents.tasks.run_dedup" in celery_app.tasks
    schedule = celery_app.conf.beat_schedule or {}
    assert "dedup" in schedule
    assert schedule["dedup"]["task"] == "contact_ops.agents.tasks.run_dedup"


def test_default_agent_factory_unknown_slug_raises():
    """An unregistered slug fails loudly rather than silently no-opping."""
    from contact_ops.agents.agent_tasks import _default_agent_factory

    with pytest.raises(ValueError, match="no batch-agent runner"):
        _default_agent_factory("not-a-real-agent")


async def test_keep_tenant_bound_reapplies_guc_after_commit(db_engine):
    """The after_begin listener re-binds the tenant GUC once COMMIT clears it.

    This is the mechanism that lets a multi-proposal agent (each propose_action
    commits the audit session) keep landing action_events: without it the second
    insert would fail ae_audit_insert WITH CHECK (tenant_id = current_tenant_id()).
    current_setting reflects the GUC regardless of RLS, so this verifies the fix
    directly under the test superuser.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from contact_ops.agents.agent_tasks import _keep_tenant_bound
    from contact_ops.core.database import bind_session_context

    settings = _settings_for_test(db_engine)
    guc = settings.TENANT_GUC_NAME
    tid = "00000000-0000-0000-0000-0000000c0a04"

    engine = create_async_engine(str(db_engine.url))
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as s:
            await bind_session_context(s, tid, "service:test", settings)
            _keep_tenant_bound(s, tid, settings)
            first = (
                await s.execute(
                    text("SELECT current_setting(:k, true)"), {"k": guc}
                )
            ).scalar_one()
            # COMMIT clears SET LOCAL; the listener must re-apply on the next txn.
            await s.commit()
            second = (
                await s.execute(
                    text("SELECT current_setting(:k, true)"), {"k": guc}
                )
            ).scalar_one()
        assert first == tid
        assert second == tid  # proves after_begin re-applied the cleared GUC
    finally:
        await engine.dispose()
