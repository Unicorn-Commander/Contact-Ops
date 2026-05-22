"""Cost guard tests.

Exercises each of the 5 layers in turn. The Postgres connection writes
``prov_activities`` rows to simulate prior burn; we then verify the
guard refuses the next call.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from contact_ops.agents.cost_guard import CostBudget, CostGuard
from contact_ops.agents.errors import CostBudgetExceededError


@pytest_asyncio.fixture
async def _tenant(db_session):
    tid = "00000000-0000-0000-0000-00000000b001"
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix)
            VALUES (CAST(:id AS uuid), 'cost-tenant', 'brand', 'Cost Tenant',
                CAST(:id AS uuid), false, 'cost-ns', 'cost-bkt')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": tid},
    )
    await db_session.commit()
    return uuid.UUID(tid)


async def _seed_burn(db_session, *, tenant_id, agent_slug, cents, hours_ago=0.0):
    """Insert a synthetic prov_activities row to simulate prior spend."""
    await db_session.execute(
        text(
            """
            INSERT INTO prov_activities (
                tenant_id, activity_type, agent_id, agent_version,
                started_at, ended_at, cost_cents
            ) VALUES (
                CAST(:t AS uuid), 'llm_extract', :a, '0.0.1',
                now() - make_interval(hours => :h),
                now() - make_interval(hours => :h),
                :c
            )
            """
        ),
        {"t": str(tenant_id), "a": agent_slug, "h": hours_ago, "c": cents},
    )
    await db_session.commit()


# ---- CostBudget validation ----

def test_cost_budget_rejects_non_positive():
    with pytest.raises(ValueError):
        CostBudget(max_tokens_per_call=0)
    with pytest.raises(ValueError):
        CostBudget(max_turns_per_run=0)
    with pytest.raises(ValueError):
        CostBudget(session_budget_cents=-1)


# ---- Layer 1: per-request tokens ----

@pytest.mark.asyncio
async def test_layer1_token_cap_fires(db_session, _tenant):
    guard = CostGuard(
        db=db_session,
        budget=CostBudget(max_tokens_per_call=100),
    )
    with pytest.raises(CostBudgetExceededError) as exc:
        await guard.check_and_record(
            agent_slug="test-agent",
            tenant_id=_tenant,
            estimated_tokens=200,
            estimated_cents=1,
        )
    assert exc.value.layer == 1


# ---- Layer 2: per-run turn counter ----

@pytest.mark.asyncio
async def test_layer2_turn_counter_fires(db_session, _tenant):
    guard = CostGuard(
        db=db_session,
        budget=CostBudget(max_turns_per_run=2),
    )
    await guard.check_and_record(
        agent_slug="t", tenant_id=_tenant, estimated_tokens=10, estimated_cents=1
    )
    guard.record_call(cents=1)
    await guard.check_and_record(
        agent_slug="t", tenant_id=_tenant, estimated_tokens=10, estimated_cents=1
    )
    guard.record_call(cents=1)
    with pytest.raises(CostBudgetExceededError) as exc:
        await guard.check_and_record(
            agent_slug="t",
            tenant_id=_tenant,
            estimated_tokens=10,
            estimated_cents=1,
        )
    assert exc.value.layer == 2


# ---- Layer 3: session budget ----

@pytest.mark.asyncio
async def test_layer3_session_budget_fires(db_session, _tenant):
    guard = CostGuard(
        db=db_session,
        budget=CostBudget(session_budget_cents=10),
    )
    await guard.check_and_record(
        agent_slug="t", tenant_id=_tenant, estimated_tokens=10, estimated_cents=8
    )
    guard.record_call(cents=8)
    with pytest.raises(CostBudgetExceededError) as exc:
        await guard.check_and_record(
            agent_slug="t",
            tenant_id=_tenant,
            estimated_tokens=10,
            estimated_cents=5,
        )
    assert exc.value.layer == 3


# ---- Layer 4: daily tenant budget ----

@pytest.mark.asyncio
async def test_layer4_daily_budget_fires(db_session, _tenant):
    await _seed_burn(
        db_session,
        tenant_id=_tenant,
        agent_slug="t",
        cents=900,
        hours_ago=2,
    )
    # Set higher inner-layer budgets so the daily-budget layer is what fires.
    guard = CostGuard(
        db=db_session,
        budget=CostBudget(
            session_budget_cents=1_000_000,
            daily_tenant_budget_cents=1000,
            monthly_tenant_budget_cents=1_000_000,
        ),
    )
    with pytest.raises(CostBudgetExceededError) as exc:
        await guard.check_and_record(
            agent_slug="t",
            tenant_id=_tenant,
            estimated_tokens=100,
            estimated_cents=200,
        )
    assert exc.value.layer == 4


# ---- Layer 5: monthly budget ----

@pytest.mark.asyncio
async def test_layer5_monthly_budget_fires(db_session, _tenant):
    await _seed_burn(
        db_session,
        tenant_id=_tenant,
        agent_slug="t",
        cents=19800,
        hours_ago=24 * 10,
    )
    guard = CostGuard(
        db=db_session,
        budget=CostBudget(
            session_budget_cents=1_000_000,
            daily_tenant_budget_cents=1_000_000,
            monthly_tenant_budget_cents=20000,
        ),
    )
    with pytest.raises(CostBudgetExceededError) as exc:
        await guard.check_and_record(
            agent_slug="t",
            tenant_id=_tenant,
            estimated_tokens=100,
            estimated_cents=500,
        )
    assert exc.value.layer == 5


# ---- Violation table writes ----

@pytest.mark.asyncio
async def test_violation_table_records_layer(db_session, _tenant):
    guard = CostGuard(
        db=db_session,
        budget=CostBudget(max_tokens_per_call=10),
    )
    with pytest.raises(CostBudgetExceededError):
        await guard.check_and_record(
            agent_slug="t",
            tenant_id=_tenant,
            estimated_tokens=100,
            estimated_cents=1,
        )
    await db_session.commit()
    row = (
        await db_session.execute(
            text(
                "SELECT layer FROM cost_budget_violation "
                "WHERE tenant_id = CAST(:t AS uuid) AND agent_slug = 't'"
            ),
            {"t": str(_tenant)},
        )
    ).mappings().first()
    assert row is not None
    assert row["layer"] == 1


# ---- Alert thresholds ----

@pytest.mark.asyncio
async def test_alert_thresholds_75_percent(db_session, _tenant):
    await _seed_burn(
        db_session,
        tenant_id=_tenant,
        agent_slug="t",
        cents=7500,
        hours_ago=240,
    )
    guard = CostGuard(
        db=db_session,
        budget=CostBudget(monthly_tenant_budget_cents=10000),
    )
    label = await guard.alert_thresholds(
        tenant_id=_tenant, agent_slug="t"
    )
    # 75% crossed -> slack; 90/95 not reached
    assert label == "slack"


@pytest.mark.asyncio
async def test_alert_thresholds_100_percent_hard_stop(db_session, _tenant):
    await _seed_burn(
        db_session,
        tenant_id=_tenant,
        agent_slug="t",
        cents=10500,
        hours_ago=240,
    )
    guard = CostGuard(
        db=db_session,
        budget=CostBudget(monthly_tenant_budget_cents=10000),
    )
    label = await guard.alert_thresholds(tenant_id=_tenant, agent_slug="t")
    assert label == "hard-stop"


@pytest.mark.asyncio
async def test_burn_rate_window(db_session, _tenant):
    await _seed_burn(
        db_session,
        tenant_id=_tenant,
        agent_slug="t",
        cents=600,
        hours_ago=0,
    )
    guard = CostGuard(
        db=db_session,
        budget=CostBudget(),
    )
    rates = await guard.get_burn_rate(
        tenant_id=_tenant, agent_slug="t", window_seconds=3600
    )
    assert rates["window_cents"] == pytest.approx(600.0)
    assert rates["cents_per_hour"] == pytest.approx(600.0)
