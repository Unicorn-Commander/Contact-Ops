"""DB-backed tests for the plan-tier lifecycle (Postgres testcontainer).

Covers the writes the pure ``test_billing_plan_tier_map.py`` cannot:
  * an operator moving a tenant free -> pro writes ``tenants.plan_tier`` AND an
    append-only ``action_event`` (the money audit trail);
  * a Lago-webhook-sourced change flips ``plan_tier`` and records source in the
    audit event's actor/evidence;
  * termination drops a paid tenant back to free;
  * an idempotent no-op (already on the tier) writes nothing.

All writes go through :func:`set_tenant_plan_tier` with the superuser
``db_session`` injected (the real admin/BYPASSRLS analogue) so the actual UPDATE
+ audit INSERT run against the migrated schema.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.billing.catalog import tier_for_lago_plan_code
from contact_ops.billing.plan_admin import (
    PlanTierError,
    set_tenant_plan_tier,
)


async def _insert_tenant(session: AsyncSession, tenant_id: uuid.UUID, tier: str = "free") -> None:
    await session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix, plan_tier)
            VALUES (:id, :slug, 'brand', 'Plan Test', :owner,
                :ns, :bkt, :tier)
            """
        ),
        {
            "id": tenant_id,
            "slug": f"plan-{tenant_id.hex[:12]}",
            "owner": tenant_id,
            "ns": f"ns-{tenant_id.hex[:8]}",
            "bkt": f"bkt-{tenant_id.hex[:8]}",
            "tier": tier,
        },
    )


async def _plan_tier(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    return await session.scalar(
        text("SELECT plan_tier FROM tenants WHERE id = :id"), {"id": tenant_id}
    )


async def _plan_events(session: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    rows = await session.execute(
        text(
            """
            SELECT event_type, actor_type::text AS actor_type, actor, payload,
                   evidence, status::text AS status
            FROM action_event
            WHERE tenant_id = :id AND event_type = 'tenant.plan_tier_change'
            ORDER BY proposed_at ASC, event_id ASC
            """
        ),
        {"id": tenant_id},
    )
    return [dict(r) for r in rows.mappings().all()]


@pytest.mark.asyncio
async def test_operator_moves_free_to_pro_is_audited(db_session: AsyncSession):
    tid = uuid.UUID("00000000-0000-0000-0000-0000000a0001")
    await _insert_tenant(db_session, tid, tier="free")

    change = await set_tenant_plan_tier(
        tenant_id=tid,
        new_tier="pro",
        settings=None,  # unused: session injected
        actor="operator:aaron",
        reason="manual upgrade",
        source="operator_cli",
        session=db_session,
    )

    assert change.changed is True
    assert change.before == "free"
    assert change.after == "pro"
    assert change.event_id is not None
    assert await _plan_tier(db_session, tid) == "pro"

    events = await _plan_events(db_session, tid)
    assert len(events) == 1
    ev = events[0]
    assert ev["status"] == "applied"
    assert ev["actor_type"] == "human"
    assert ev["actor"]["source"] == "operator_cli"
    assert ev["payload"]["before"]["plan_tier"] == "free"
    assert ev["payload"]["after"]["plan_tier"] == "pro"
    assert ev["evidence"]["reason"] == "manual upgrade"


@pytest.mark.asyncio
async def test_lago_webhook_activation_flips_plan_tier(db_session: AsyncSession):
    tid = uuid.UUID("00000000-0000-0000-0000-0000000a0002")
    await _insert_tenant(db_session, tid, tier="free")

    # Resolve the tier exactly as the webhook does.
    tier = tier_for_lago_plan_code("professional")
    assert tier == "pro"

    change = await set_tenant_plan_tier(
        tenant_id=tid,
        new_tier=tier,
        settings=None,
        actor="lago_webhook",
        reason="lago subscription.created plan_code=professional",
        source="lago_webhook",
        session=db_session,
    )

    assert change.changed is True
    assert await _plan_tier(db_session, tid) == "pro"
    events = await _plan_events(db_session, tid)
    assert len(events) == 1
    # A payment event is an external system in the audit trail.
    assert events[0]["actor_type"] == "external_system"
    assert events[0]["evidence"]["source"] == "lago_webhook"


@pytest.mark.asyncio
async def test_lago_termination_downgrades_to_free(db_session: AsyncSession):
    tid = uuid.UUID("00000000-0000-0000-0000-0000000a0003")
    await _insert_tenant(db_session, tid, tier="pro")

    change = await set_tenant_plan_tier(
        tenant_id=tid,
        new_tier="free",  # termination target
        settings=None,
        actor="lago_webhook",
        reason="lago subscription.terminated plan_code=professional",
        source="lago_webhook",
        session=db_session,
    )

    assert change.before == "pro"
    assert change.after == "free"
    assert await _plan_tier(db_session, tid) == "free"


@pytest.mark.asyncio
async def test_setting_same_tier_is_noop_with_no_event(db_session: AsyncSession):
    tid = uuid.UUID("00000000-0000-0000-0000-0000000a0004")
    await _insert_tenant(db_session, tid, tier="pro")

    change = await set_tenant_plan_tier(
        tenant_id=tid,
        new_tier="pro",
        settings=None,
        actor="operator:aaron",
        reason="noop",
        source="operator_cli",
        session=db_session,
    )

    assert change.changed is False
    assert change.event_id is None
    assert await _plan_tier(db_session, tid) == "pro"
    assert await _plan_events(db_session, tid) == []


@pytest.mark.asyncio
async def test_unknown_tenant_raises(db_session: AsyncSession):
    with pytest.raises(PlanTierError):
        await set_tenant_plan_tier(
            tenant_id=uuid.UUID("00000000-0000-0000-0000-0000000affff"),
            new_tier="pro",
            settings=None,
            actor="operator:aaron",
            reason="x",
            source="operator_cli",
            session=db_session,
        )


@pytest.mark.asyncio
async def test_invalid_tier_rejected_before_db(db_session: AsyncSession):
    tid = uuid.UUID("00000000-0000-0000-0000-0000000a0006")
    await _insert_tenant(db_session, tid, tier="free")
    with pytest.raises(PlanTierError):
        await set_tenant_plan_tier(
            tenant_id=tid,
            new_tier="platinum",  # not a valid tier
            settings=None,
            actor="operator:aaron",
            reason="x",
            source="operator_cli",
            session=db_session,
        )
    # Unchanged.
    assert await _plan_tier(db_session, tid) == "free"


@pytest.mark.asyncio
async def test_audit_chain_links_to_prior_event(db_session: AsyncSession):
    """Two consecutive changes hash-chain: the 2nd event's prev == 1st's hash."""
    tid = uuid.UUID("00000000-0000-0000-0000-0000000a0007")
    await _insert_tenant(db_session, tid, tier="free")

    await set_tenant_plan_tier(
        tenant_id=tid, new_tier="pro", settings=None, actor="op",
        reason="up", source="operator_cli", session=db_session,
    )
    await set_tenant_plan_tier(
        tenant_id=tid, new_tier="enterprise", settings=None, actor="op",
        reason="up2", source="operator_cli", session=db_session,
    )

    rows = await db_session.execute(
        text(
            """
            SELECT content_hash, prev_event_hash
            FROM action_event
            WHERE tenant_id = :id AND event_type = 'tenant.plan_tier_change'
            ORDER BY proposed_at ASC, event_id ASC
            """
        ),
        {"id": tid},
    )
    events = rows.mappings().all()
    assert len(events) == 2
    assert events[1]["prev_event_hash"] == events[0]["content_hash"]
