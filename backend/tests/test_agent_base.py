"""End-to-end test of BaseAgent.execute + propose_action.

Uses the EchoAgent fixture and the test-container Postgres. Covers:

* a BaseAgent run wrapped in an OTel span emits one action_event row
* the row carries the new Phase 3 columns (idempotency_key, decision_payload,
  reversibility_class, trust_tier_at_creation, triggered_by)
* the auto-apply policy honors (tier, confidence, reversibility) — high
  confidence + reversible at T2+ auto-applies; same call at T0 stays proposed
* a replay (same input) reuses the existing event_id rather than writing a
  duplicate
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from contact_ops.agents import Reversibility, TrustTier
from contact_ops.agents.base import AgentContext, Visibility
from contact_ops.agents.cost_guard import CostBudget, CostGuard
from contact_ops.agents.registry import _clear_registry_for_tests
from tests.fixtures.echo_agent import register_echo_agent


@pytest.fixture(autouse=True)
def _clear_registry():
    _clear_registry_for_tests()
    yield
    _clear_registry_for_tests()


def _make_breaker_stub():
    """A circuit breaker stub that never trips and never writes to redis."""
    stub = MagicMock()
    stub.check = AsyncMock(return_value=None)
    stub.evaluate = AsyncMock(return_value=None)
    return stub


async def _make_ctx(
    *,
    db_session,
    tenant_id: str,
    event_payload: dict,
    visibility: Visibility = Visibility.PRIVATE,
    budget: CostBudget = CostBudget(),
) -> AgentContext:
    return AgentContext(
        db=db_session,
        audit_db=db_session,
        tenant_id=uuid.UUID(tenant_id),
        visibility=visibility,
        triggered_by="manual",
        event_payload=event_payload,
        cost_guard=CostGuard(db=db_session, budget=budget),
        circuit_breaker=_make_breaker_stub(),
    )


@pytest_asyncio.fixture
async def _seeded_tenant(db_session):
    """Insert a tenant the agent runs against."""
    tid = "00000000-0000-0000-0000-00000000a001"
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix)
            VALUES (CAST(:id AS uuid), 'echo-tenant', 'brand', 'Echo Tenant',
                CAST(:id AS uuid), false, 'echo-ns', 'echo-bkt')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": tid},
    )
    await db_session.commit()
    return tid


@pytest.mark.asyncio
async def test_execute_emits_action_event(db_session, _seeded_tenant):
    agent = register_echo_agent()
    ctx = await _make_ctx(
        db_session=db_session,
        tenant_id=_seeded_tenant,
        event_payload={"message": "hello", "confidence": 0.5},
    )
    result = await agent.execute(ctx=ctx)
    assert result.proposals_emitted == 1
    assert result.proposals_auto_applied == 0  # T0 -> never auto-applies

    rows = (
        await db_session.execute(
            text(
                """
                SELECT event_type, status, confidence, reversibility_class,
                       trust_tier_at_creation, triggered_by, idempotency_key
                FROM action_event
                WHERE tenant_id = CAST(:t AS uuid)
                  AND event_type = 'echo.propose'
                """
            ),
            {"t": _seeded_tenant},
        )
    ).mappings().all()
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "echo.propose"
    assert row["status"] == "proposed"
    assert row["confidence"] == pytest.approx(0.5)
    assert row["reversibility_class"] == "reversible"
    assert row["trust_tier_at_creation"] == 0
    assert row["triggered_by"] == "manual"
    assert row["idempotency_key"] is not None


@pytest.mark.asyncio
async def test_auto_apply_at_t3_with_high_confidence(db_session, _seeded_tenant):
    """T3 + reversible + confidence >= 0.85 -> status=applied."""
    agent = register_echo_agent(initial_trust_tier=TrustTier.T3_SENIOR)
    ctx = await _make_ctx(
        db_session=db_session,
        tenant_id=_seeded_tenant,
        event_payload={
            "message": "hi-conf",
            "confidence": 0.92,
            "reversibility": Reversibility.REVERSIBLE.value,
        },
    )
    result = await agent.execute(ctx=ctx)
    assert result.proposals_auto_applied == 1
    row = (
        await db_session.execute(
            text(
                "SELECT status, applied_at FROM action_event "
                "WHERE tenant_id = CAST(:t AS uuid) AND event_type='echo.propose'"
            ),
            {"t": _seeded_tenant},
        )
    ).mappings().one()
    assert row["status"] == "applied"
    assert row["applied_at"] is not None


@pytest.mark.asyncio
async def test_irreversible_never_auto_applies_even_at_t4(db_session, _seeded_tenant):
    """T4 + confidence=1.0 + IRREVERSIBLE -> still status=proposed."""
    agent = register_echo_agent(initial_trust_tier=TrustTier.T4_PRINCIPAL)
    ctx = await _make_ctx(
        db_session=db_session,
        tenant_id=_seeded_tenant,
        event_payload={
            "message": "danger",
            "confidence": 1.0,
            "reversibility": Reversibility.IRREVERSIBLE.value,
        },
    )
    result = await agent.execute(ctx=ctx)
    assert result.proposals_auto_applied == 0
    row = (
        await db_session.execute(
            text(
                "SELECT status FROM action_event "
                "WHERE tenant_id = CAST(:t AS uuid) AND event_type='echo.propose'"
            ),
            {"t": _seeded_tenant},
        )
    ).mappings().one()
    assert row["status"] == "proposed"


@pytest.mark.asyncio
async def test_replay_is_idempotent(db_session, _seeded_tenant):
    """Same input + same agent -> the second execute returns 'replayed'."""
    agent = register_echo_agent()
    fixed_aggregate = str(uuid.uuid4())
    payload = {
        "aggregate_id": fixed_aggregate,
        "message": "ping",
        "confidence": 0.5,
    }
    ctx1 = await _make_ctx(
        db_session=db_session,
        tenant_id=_seeded_tenant,
        event_payload=payload,
    )
    result1 = await agent.execute(ctx=ctx1)
    assert result1.extra["status"] == "proposed"

    # The same input via a second invocation should be a replay.
    ctx2 = await _make_ctx(
        db_session=db_session,
        tenant_id=_seeded_tenant,
        event_payload=payload,
    )
    result2 = await agent.execute(ctx=ctx2)
    assert result2.extra["status"] == "replayed"
    assert result2.extra["event_id"] == result1.extra["event_id"]

    count = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM action_event "
                "WHERE tenant_id = CAST(:t AS uuid) AND event_type='echo.propose'"
            ),
            {"t": _seeded_tenant},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_decision_payload_captured(db_session, _seeded_tenant):
    """decision_payload should mirror what the agent decided."""
    agent = register_echo_agent(initial_trust_tier=TrustTier.T2_TRUSTED)
    ctx = await _make_ctx(
        db_session=db_session,
        tenant_id=_seeded_tenant,
        event_payload={"message": "decided", "confidence": 0.99},
    )
    await agent.execute(ctx=ctx)
    row = (
        await db_session.execute(
            text(
                """
                SELECT decision_payload, trust_tier_at_creation, status
                FROM action_event
                WHERE tenant_id = CAST(:t AS uuid) AND event_type='echo.propose'
                """
            ),
            {"t": _seeded_tenant},
        )
    ).mappings().one()
    payload = row["decision_payload"]
    assert payload["reversibility"] == "reversible"
    assert payload["tier_at_creation"] == 2
    assert payload["auto_apply_eligible"] is True
    assert payload["payload_after"]["echoed"] == "decided"
    assert row["status"] == "applied"


@pytest.mark.asyncio
async def test_invalid_confidence_raises(db_session, _seeded_tenant):
    agent = register_echo_agent()
    ctx = await _make_ctx(
        db_session=db_session,
        tenant_id=_seeded_tenant,
        event_payload={"message": "bad", "confidence": 1.5},
    )
    with pytest.raises(ValueError):
        await agent.execute(ctx=ctx)
