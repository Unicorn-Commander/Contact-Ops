"""Agent-execution bridge: run a BATCH agent through BaseAgent.execute per tenant.

Covers contact_ops.agents.agent_tasks, the first production AgentContext
construction site. The bridge enumerates tenants, builds an app-role + audit-role
session bound to each, and runs the agent through the full governance path.

The tests use a lightweight multi-proposal agent (no pandas / Splink) so they
exercise the bridge mechanics, NOT the dedup pipeline:

* per-tenant execution: the agent runs once per committed tenant and its
  action_events are written scoped to that tenant
* the GUC-after-commit path: an agent that emits TWO proposals (two audit
  commits) still lands both
* the workspace kill-switch stops a paused tenant without aborting the others
* one tenant's failure is isolated, the run continues
* run_dedup is dormant unless DEDUP_AGENT_ENABLED

SEEDING: the bridge opens its OWN engines (the way mcp/server.py builds a request
context), so it cannot see a rolled-back fixture transaction. Seeds + assertions
+ cleanup therefore use a superuser sync engine with REAL commits, mirroring
tests/test_inbox_snooze_flipper.py. The bridge's three DSNs point at the
testcontainer superuser, so the audit role's ae_audit_insert RLS policy is not
enforced here; these tests verify the bridge tenant loop, the workspace
kill-switch (an app-level check, role-independent), failure isolation, and the
GUC re-binding MECHANISM (test_keep_tenant_bound, via current_setting). End-to-end
enforcement under the real contact_ops_audit role is the live dogfood gate.
"""
# ruff: noqa: E501

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text

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


@pytest.fixture(autouse=True)
def _clear_registry():
    _clear_registry_for_tests()
    yield
    _clear_registry_for_tests()


def _sync_su_url(async_url: str) -> str:
    """Superuser sync DSN for committed seed / assert / cleanup + enumeration."""
    return async_url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def su_engine(postgres_container: str):
    """Superuser sync engine for committed seed / assert / cleanup."""
    eng = create_engine(_sync_su_url(postgres_container), future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def seeded_ids():
    """Track tenant ids seeded with real commits so they can be cleaned up."""
    return []


@pytest.fixture(autouse=True)
def _cleanup(su_engine, seeded_ids):
    yield
    if not seeded_ids:
        return
    with su_engine.begin() as c:
        c.execute(
            text("DELETE FROM action_event WHERE tenant_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": seeded_ids},
        )
        c.execute(
            text("DELETE FROM tenants WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": seeded_ids},
        )


def _seed_tenant(
    su_engine, seeded_ids, tid: str, slug: str, *, agents_paused: bool = False
) -> None:
    with su_engine.begin() as c:
        c.execute(
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
    seeded_ids.append(tid)


def _count_events(su_engine, tid: str) -> int:
    with su_engine.connect() as c:
        return c.execute(
            text(
                "SELECT COUNT(*) FROM action_event "
                "WHERE tenant_id = CAST(:t AS uuid) AND event_type = 'echo.propose'"
            ),
            {"t": tid},
        ).scalar_one()


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
    propose_action COMMITs the audit session, clearing SET LOCAL.
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


def _bridge_settings(postgres_container: str) -> Any:
    """Settings whose three DSNs point at the testcontainer superuser.

    model_copy(update=...) returns an ISOLATED instance, never mutating the
    lru_cached global. postgres_container already carries the password (no
    masking, unlike str(engine.url)).
    """
    from contact_ops.core.config import get_settings

    return get_settings().model_copy(
        update={
            "DATABASE_URL": postgres_container,
            "AUDIT_DATABASE_URL": postgres_container,
            "MIGRATION_DATABASE_URL": _sync_su_url(postgres_container),
        }
    )


async def test_runs_per_tenant_and_emits(su_engine, seeded_ids, postgres_container):
    from contact_ops.agents.agent_tasks import _run_agent_all_tenants

    a = "00000000-0000-0000-0000-0000000c0a01"
    b = "00000000-0000-0000-0000-0000000c0b01"
    _seed_tenant(su_engine, seeded_ids, a, "bridge-a")
    _seed_tenant(su_engine, seeded_ids, b, "bridge-b")

    register_agent(_multi_def())
    agent = MultiProposalAgent(_multi_def())

    totals = await _run_agent_all_tenants(
        "multi-echo",
        _bridge_settings(postgres_container),
        agent_factory=_factory_for(agent),
        redis_client=_fake_redis(),
    )

    assert totals["failed"] == 0
    # Each seeded tenant ran and got exactly 2 events scoped to it (the second
    # propose_action's commit did not lose the audit binding).
    assert _count_events(su_engine, a) == 2
    assert _count_events(su_engine, b) == 2
    assert totals["proposals"] >= 4


async def test_kill_switch_skips_paused_tenant(su_engine, seeded_ids, postgres_container):
    from contact_ops.agents.agent_tasks import _run_agent_all_tenants

    paused = "00000000-0000-0000-0000-0000000c0a02"
    active = "00000000-0000-0000-0000-0000000c0b02"
    _seed_tenant(su_engine, seeded_ids, paused, "bridge-paused", agents_paused=True)
    _seed_tenant(su_engine, seeded_ids, active, "bridge-active", agents_paused=False)

    register_agent(_multi_def())
    agent = MultiProposalAgent(_multi_def())

    totals = await _run_agent_all_tenants(
        "multi-echo",
        _bridge_settings(postgres_container),
        agent_factory=_factory_for(agent),
        redis_client=_fake_redis(),
    )

    # The paused tenant raised WorkspaceAgentsPausedError inside execute (counted
    # as a skipped/failed tenant); the active one ran and emitted.
    assert _count_events(su_engine, paused) == 0
    assert _count_events(su_engine, active) == 2
    assert totals["ran"] >= 1
    assert totals["failed"] >= 1


async def test_one_tenant_failure_is_isolated(su_engine, seeded_ids, postgres_container):
    from contact_ops.agents.agent_tasks import _run_agent_all_tenants

    a = "00000000-0000-0000-0000-0000000c0a03"
    _seed_tenant(su_engine, seeded_ids, a, "bridge-boom")

    register_agent(_multi_def())

    totals = await _run_agent_all_tenants(
        "multi-echo",
        _bridge_settings(postgres_container),
        agent_factory=_factory_for(BoomAgent(_multi_def())),
        redis_client=_fake_redis(),
    )

    # The agent raised for the seeded tenant; the run completed (did not
    # propagate) and reported the failure, emitting nothing.
    assert totals["failed"] >= 1
    assert _count_events(su_engine, a) == 0


async def test_keep_tenant_bound_reapplies_guc_after_commit(postgres_container):
    """The after_begin listener re-binds the tenant GUC once COMMIT clears it.

    This is the mechanism that lets a multi-proposal agent (each propose_action
    commits the audit session) keep landing action_events: without it the second
    insert would fail ae_audit_insert WITH CHECK (tenant_id = current_tenant_id()).
    current_setting reflects the GUC regardless of RLS, so this verifies the fix
    directly.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from contact_ops.agents.agent_tasks import _keep_tenant_bound
    from contact_ops.core.database import bind_session_context

    settings = _bridge_settings(postgres_container)
    guc = settings.TENANT_GUC_NAME
    tid = "00000000-0000-0000-0000-0000000c0a04"

    engine = create_async_engine(postgres_container)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as s:
            await bind_session_context(s, tid, "service:test", settings)
            _keep_tenant_bound(s, tid, settings)
            first = (
                await s.execute(text("SELECT current_setting(:k, true)"), {"k": guc})
            ).scalar_one()
            # COMMIT clears SET LOCAL; the listener must re-apply on the next txn.
            await s.commit()
            second = (
                await s.execute(text("SELECT current_setting(:k, true)"), {"k": guc})
            ).scalar_one()
        assert first == tid
        assert second == tid  # proves after_begin re-applied the cleared GUC
    finally:
        await engine.dispose()


async def test_run_dedup_dormant_by_default():
    """DEDUP_AGENT_ENABLED defaults False, so run_dedup no-ops without DB I/O."""
    from contact_ops.agents.agent_tasks import run_dedup

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
