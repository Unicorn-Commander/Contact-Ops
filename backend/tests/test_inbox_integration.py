"""DB integration tests for Phase 3.3a inbox MCP tools.

Exercises end-to-end paths against the test Postgres:
* approve happy path + STALE_TIER + ALREADY_RESOLVED + typed-phrase gating
* reject mute creates suppression rule; undo within / outside window
* snooze sets snoozed_until column
* conflict-cancels-snooze trigger fires
* list_pending_proposals basic + filter
* revert_auto_applied within 5min window
* suppression_rules.is_suppressed match
"""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.schemas.inbox import ListPendingProposalsInput
from contact_ops.services.inbox_mutations import (
    UNDO_WINDOW,
    InboxMutationError,
    approve_proposal,
    bulk_approve,
    reject_proposal,
    revert_auto_applied,
    snooze_proposal,
)
from contact_ops.services.inbox_query import (
    list_pending_proposals,
)
from contact_ops.services.suppression_rules import (
    create_suppression_rule,
    is_suppressed,
    list_suppression_rules,
)

pytestmark = pytest.mark.asyncio


# ---- helpers ----


async def _make_tenant(db: AsyncSession, *, hipaa: bool = False) -> uuid.UUID:
    tid = uuid.uuid4()
    await db.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix)
            VALUES (CAST(:id AS uuid), :slug, 'brand', :name, CAST(:id AS uuid),
                :hipaa, :ns, :bkt)
            """
        ),
        {
            "id": str(tid),
            "slug": f"inbox-test-{tid.hex[:8]}",
            "name": f"Test {tid.hex[:8]}",
            "hipaa": hipaa,
            "ns": f"ns-{tid.hex[:8]}",
            "bkt": f"bkt-{tid.hex[:8]}",
        },
    )
    return tid


async def _make_person(db: AsyncSession, *, tenant_id: uuid.UUID, display_name: str) -> uuid.UUID:
    pid = uuid.uuid4()
    await db.execute(
        text(
            """
            INSERT INTO persons (id, display_name, kind, canonical_owner_tenant_id, created_at)
            VALUES (CAST(:id AS uuid), :name, 'individual', CAST(:tenant AS uuid), now())
            """
        ),
        {"id": str(pid), "name": display_name, "tenant": str(tenant_id)},
    )
    return pid


async def _make_proposal(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    aggregate_id: uuid.UUID,
    agent_slug: str = "enrichment",
    event_type: str = "enrichment.field_set",
    confidence: float = 0.85,
    reversibility: str = "reversible",
    payload_after: dict | None = None,
    status: str = "proposed",
    applied_at: datetime | None = None,
    snoozed_until: datetime | None = None,
    target_tenant_id: uuid.UUID | None = None,
    tier: int = 2,
) -> uuid.UUID:
    eid = uuid.uuid4()
    payload_after = payload_after or {"display_name": "Updated Name"}
    actor = {"sub": agent_slug, "agent_version": "test-1.0", "act": {"sub": "system"}}
    decision_payload = {
        "reversibility": reversibility,
        "tier_at_creation": tier,
        "auto_apply_eligible": (status == "applied"),
        "payload_after": payload_after,
        "payload_before": None,
    }
    raw_idem = f"{eid}|{agent_slug}|{tenant_id}|{aggregate_id}|{event_type}"
    content_hash = hashlib.sha256(json.dumps(payload_after, sort_keys=True).encode()).digest()
    await db.execute(
        text(
            """
            INSERT INTO action_event (
                event_id, event_type, event_version,
                tenant_id, target_tenant_id, aggregate_type, aggregate_id,
                payload, actor, actor_type,
                confidence, evidence, rationale,
                status, content_hash, idempotency_key,
                decision_payload, reversibility_class,
                agent_version, trust_tier_at_creation, triggered_by,
                applied_at, snoozed_until
            ) VALUES (
                CAST(:event_id AS uuid), :event_type, 1,
                CAST(:tenant AS uuid), CAST(:target_tenant AS uuid),
                CAST(:agg_type AS entity_kind), CAST(:agg_id AS uuid),
                CAST(:payload AS jsonb), CAST(:actor AS jsonb), 'agent'::actor_type,
                :confidence, '{}'::jsonb, :rationale,
                CAST(:status AS event_status), :content_hash, :idempotency_key,
                CAST(:decision_payload AS jsonb), :reversibility,
                'test-1.0', :tier, 'integration-test',
                :applied_at, :snoozed_until
            )
            """
        ),
        {
            "event_id": str(eid),
            "event_type": event_type,
            "tenant": str(tenant_id),
            "target_tenant": str(target_tenant_id) if target_tenant_id else None,
            "agg_type": "person",
            "agg_id": str(aggregate_id),
            "payload": json.dumps(payload_after),
            "actor": json.dumps(actor),
            "confidence": confidence,
            "rationale": "Integration test proposal",
            "status": status,
            "content_hash": content_hash,
            "idempotency_key": hashlib.sha256(raw_idem.encode()).hexdigest(),
            "decision_payload": json.dumps(decision_payload),
            "reversibility": reversibility,
            "tier": tier,
            "applied_at": applied_at,
            "snoozed_until": snoozed_until,
        },
    )
    return eid


# ---- approve_proposal ----


async def test_approve_proposal_happy_path(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Test Person")
    eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person,
        confidence=0.92, reversibility="reversible", tier=2,
    )
    reviewer = uuid.uuid4()

    result = await approve_proposal(
        db=db_session,
        tenant_id=tenant,
        reviewer_id=reviewer,
        proposal_id=eid,
        tier_assigned=1,  # high-conf reversible -> server says 1
        typed_phrase=None,
        field_choices=None,
        custom_values=None,
        time_to_decide_sec=5,
        keyboard_path=True,
        typed_phrase_used=False,
    )
    assert result["applied"] is True
    assert result["action_event_id"] == eid

    # status flipped + inbox_decisions row
    status_row = await db_session.execute(
        text("SELECT status FROM action_event WHERE event_id = CAST(:id AS uuid)"),
        {"id": str(eid)},
    )
    assert status_row.scalar_one() == "applied"

    decision_row = await db_session.execute(
        text(
            """
            SELECT decision, tier_assigned, keyboard_path, reviewer_id
            FROM inbox_decisions WHERE proposal_id = CAST(:id AS uuid)
            """
        ),
        {"id": str(eid)},
    )
    d = decision_row.mappings().first()
    assert d is not None
    assert d["decision"] == "approve"
    assert d["tier_assigned"] == 1
    assert d["keyboard_path"] is True
    assert uuid.UUID(str(d["reviewer_id"])) == reviewer


async def test_approve_proposal_stale_tier_rejected(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Stale Tier Test")
    eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person,
        confidence=0.99, reversibility="reversible",
    )

    with pytest.raises(InboxMutationError) as exc:
        await approve_proposal(
            db=db_session,
            tenant_id=tenant,
            reviewer_id=uuid.uuid4(),
            proposal_id=eid,
            tier_assigned=4,  # client says 4, server should say 1
            typed_phrase=None,
            field_choices=None,
            custom_values=None,
            time_to_decide_sec=None,
            keyboard_path=False,
            typed_phrase_used=False,
        )
    assert exc.value.code == "STALE_TIER_POLICY"


async def test_approve_proposal_already_resolved(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Already Resolved")
    eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person, status="rejected",
    )
    with pytest.raises(InboxMutationError) as exc:
        await approve_proposal(
            db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
            proposal_id=eid, tier_assigned=1, typed_phrase=None,
            field_choices=None, custom_values=None,
            time_to_decide_sec=None, keyboard_path=False, typed_phrase_used=False,
        )
    assert exc.value.code == "ALREADY_RESOLVED"


async def test_approve_proposal_hipaa_requires_typed_phrase(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session, hipaa=True)
    person = await _make_person(db_session, tenant_id=tenant, display_name="HIPAA Person")
    eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person, confidence=0.99,
    )

    with pytest.raises(InboxMutationError) as exc:
        await approve_proposal(
            db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
            proposal_id=eid, tier_assigned=4, typed_phrase=None,
            field_choices=None, custom_values=None,
            time_to_decide_sec=None, keyboard_path=False, typed_phrase_used=False,
        )
    assert exc.value.code == "TYPED_PHRASE_MISMATCH"


async def test_approve_proposal_hipaa_accepts_typed_phrase(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session, hipaa=True)
    person = await _make_person(db_session, tenant_id=tenant, display_name="HIPAA Person 2")
    eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person, confidence=0.99,
    )
    result = await approve_proposal(
        db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
        proposal_id=eid, tier_assigned=4, typed_phrase="approve hipaa",
        field_choices=None, custom_values=None,
        time_to_decide_sec=None, keyboard_path=True, typed_phrase_used=True,
    )
    assert result["applied"] is True


# ---- reject_proposal ----


async def test_reject_proposal_mute_creates_suppression_rule(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Mute Test")
    eid = await _make_proposal(db_session, tenant_id=tenant, aggregate_id=person)

    result = await reject_proposal(
        db=db_session,
        tenant_id=tenant,
        reviewer_id=uuid.uuid4(),
        proposal_id=eid,
        mode="mute",
        reason="agent is wrong about this person",
        tier_assigned=1,
        time_to_decide_sec=None,
        keyboard_path=True,
        suppression_aggregate_id=person,
        suppression_field_name="title",
        suppression_expires_at=None,
    )
    assert result["rejected"] is True
    assert result["suppression_rule_id"] is not None

    # Verify rule exists and is_suppressed returns True
    suppressed = await is_suppressed(
        db=db_session,
        tenant_id=tenant,
        agent_slug="enrichment",
        aggregate_id=person,
        field_names=["title"],
    )
    assert suppressed is True


async def test_reject_proposal_undo_within_window(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Undo Test")
    eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person,
        status="applied", applied_at=datetime.now(UTC) - timedelta(seconds=5),
    )

    result = await reject_proposal(
        db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
        proposal_id=eid, mode="undo", reason=None, tier_assigned=1,
        time_to_decide_sec=None, keyboard_path=True,
        suppression_aggregate_id=None, suppression_field_name=None,
        suppression_expires_at=None,
    )
    assert result["rejected"] is True

    status_row = await db_session.execute(
        text("SELECT status FROM action_event WHERE event_id = CAST(:id AS uuid)"),
        {"id": str(eid)},
    )
    assert status_row.scalar_one() == "rejected"


async def test_reject_proposal_undo_outside_window(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Outside Window")
    eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person,
        status="applied",
        applied_at=datetime.now(UTC) - (UNDO_WINDOW + timedelta(seconds=10)),
    )
    with pytest.raises(InboxMutationError) as exc:
        await reject_proposal(
            db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
            proposal_id=eid, mode="undo", reason=None, tier_assigned=1,
            time_to_decide_sec=None, keyboard_path=False,
            suppression_aggregate_id=None, suppression_field_name=None,
            suppression_expires_at=None,
        )
    assert exc.value.code == "UNDO_WINDOW_EXPIRED"


# ---- snooze ----


async def test_snooze_proposal_sets_snoozed_until(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Snooze Test")
    eid = await _make_proposal(db_session, tenant_id=tenant, aggregate_id=person)
    target = datetime.now(UTC) + timedelta(hours=2)

    result = await snooze_proposal(
        db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
        proposal_id=eid, snooze_until=target, snooze_reason="tomorrow",
        pegged_event_id=None, tier_assigned=1, keyboard_path=True,
    )
    assert result["snoozed"] is True

    row = await db_session.execute(
        text("SELECT snoozed_until FROM action_event WHERE event_id = CAST(:id AS uuid)"),
        {"id": str(eid)},
    )
    snoozed_until = row.scalar_one()
    assert snoozed_until is not None
    assert abs((snoozed_until - target).total_seconds()) < 2.0


async def test_snooze_in_past_rejected(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Past Snooze")
    eid = await _make_proposal(db_session, tenant_id=tenant, aggregate_id=person)
    past = datetime.now(UTC) - timedelta(minutes=5)
    with pytest.raises(InboxMutationError) as exc:
        await snooze_proposal(
            db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
            proposal_id=eid, snooze_until=past, snooze_reason="custom",
            pegged_event_id=None, tier_assigned=1, keyboard_path=False,
        )
    assert exc.value.code == "INVALID_SNOOZE_UNTIL"


# ---- conflict-cancels-snooze trigger ----


async def test_conflict_trigger_clears_snooze(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Conflict Trigger")
    snoozed_eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person,
        snoozed_until=datetime.now(UTC) + timedelta(hours=1),
    )
    other_eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person,
        agent_slug="meeting-ops",
    )
    # Insert a proposal_conflict row -> trigger should clear snoozed_until
    await db_session.execute(
        text(
            """
            INSERT INTO proposal_conflict (
                tenant_id, primary_proposal_id, conflicting_proposal_id,
                conflict_type, entity_id, field_name
            ) VALUES (
                CAST(:tenant AS uuid), CAST(:a AS uuid), CAST(:b AS uuid),
                'contradicting_field_value', CAST(:entity AS uuid), 'display_name'
            )
            """
        ),
        {
            "tenant": str(tenant), "a": str(snoozed_eid), "b": str(other_eid),
            "entity": str(person),
        },
    )

    row = await db_session.execute(
        text("SELECT snoozed_until FROM action_event WHERE event_id = CAST(:id AS uuid)"),
        {"id": str(snoozed_eid)},
    )
    assert row.scalar_one() is None  # trigger cleared it


# ---- revert_auto_applied ----


async def test_revert_auto_applied_within_window(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Revert Test")
    eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person,
        status="applied", applied_at=datetime.now(UTC) - timedelta(seconds=30),
    )
    result = await revert_auto_applied(
        db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
        proposal_id=eid, reason="agent was wrong",
    )
    assert result["reverted"] is True
    assert result["age_seconds"] >= 25

    status_row = await db_session.execute(
        text("SELECT status FROM action_event WHERE event_id = CAST(:id AS uuid)"),
        {"id": str(eid)},
    )
    assert status_row.scalar_one() == "reverted"


async def test_revert_auto_applied_outside_window(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Revert Late")
    eid = await _make_proposal(
        db_session, tenant_id=tenant, aggregate_id=person,
        status="applied",
        applied_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    with pytest.raises(InboxMutationError) as exc:
        await revert_auto_applied(
            db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
            proposal_id=eid, reason=None,
        )
    assert exc.value.code == "REVERT_WINDOW_EXPIRED"


# ---- suppression rules ----


async def test_suppression_rule_match(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Suppression Test")
    rule_id = await create_suppression_rule(
        db=db_session,
        tenant_id=tenant,
        agent_slug="enrichment",
        aggregate_type="person",
        aggregate_id=person,
        field_name="title",
        created_by=uuid.uuid4(),
    )
    assert isinstance(rule_id, uuid.UUID)
    suppressed = await is_suppressed(
        db=db_session,
        tenant_id=tenant,
        agent_slug="enrichment",
        aggregate_id=person,
        field_names=["title"],
    )
    assert suppressed is True
    not_suppressed = await is_suppressed(
        db=db_session,
        tenant_id=tenant,
        agent_slug="enrichment",
        aggregate_id=person,
        field_names=["email"],
    )
    assert not_suppressed is False


async def test_suppression_list_excludes_expired(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Expired Test")
    await create_suppression_rule(
        db=db_session, tenant_id=tenant, agent_slug="enrichment",
        aggregate_type="person", aggregate_id=person, field_name="x",
        created_by=uuid.uuid4(),
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    rules = await list_suppression_rules(db=db_session, tenant_id=tenant)
    assert len(rules) == 0
    rules_all = await list_suppression_rules(
        db=db_session, tenant_id=tenant, include_expired=True
    )
    assert len(rules_all) == 1


# ---- bulk_approve ----


async def test_bulk_approve_partial_failure(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Bulk Test")
    # 3 proposed + 1 already-applied
    pids = []
    for _ in range(3):
        pids.append(await _make_proposal(db_session, tenant_id=tenant, aggregate_id=person))
    pids.append(
        await _make_proposal(
            db_session, tenant_id=tenant, aggregate_id=person, status="applied",
            applied_at=datetime.now(UTC),
        )
    )

    result = await bulk_approve(
        db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
        proposal_ids=pids, typed_phrase=None, tier_assigned=1,
        time_to_decide_sec=None, keyboard_path=True,
    )
    assert result["applied"] == 3
    assert len(result["skipped"]) == 1
    assert result["reasons"][str(pids[3])] == "already_resolved"


async def test_bulk_approve_over_ten_requires_typed_phrase(db_session: AsyncSession) -> None:
    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Bulk Over Ten")
    pids = [
        await _make_proposal(db_session, tenant_id=tenant, aggregate_id=person)
        for _ in range(11)
    ]
    with pytest.raises(InboxMutationError) as exc:
        await bulk_approve(
            db=db_session, tenant_id=tenant, reviewer_id=uuid.uuid4(),
            proposal_ids=pids, typed_phrase=None, tier_assigned=1,
            time_to_decide_sec=None, keyboard_path=False,
        )
    assert exc.value.code == "TYPED_PHRASE_MISMATCH"


# ---- list_pending_proposals ----


async def test_list_pending_returns_seeded_proposals(db_session: AsyncSession) -> None:
    import fakeredis.aioredis  # type: ignore[import-untyped]

    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="List Test")
    for _ in range(3):
        await _make_proposal(db_session, tenant_id=tenant, aggregate_id=person)

    redis = fakeredis.aioredis.FakeRedis()
    try:
        out = await list_pending_proposals(
            db=db_session, redis=redis, caller_tenant_id=tenant,
            payload=ListPendingProposalsInput(status="proposed", limit=10),
        )
    finally:
        await redis.aclose()
    assert len(out.proposals) == 3
    assert len(out.clusters) == 1
    assert out.clusters[0].entity_id == person
    assert set(out.clusters[0].agent_slugs) == {"enrichment"}


async def test_list_pending_filters_by_agent(db_session: AsyncSession) -> None:
    import fakeredis.aioredis  # type: ignore[import-untyped]

    tenant = await _make_tenant(db_session)
    person = await _make_person(db_session, tenant_id=tenant, display_name="Filter Test")
    await _make_proposal(db_session, tenant_id=tenant, aggregate_id=person, agent_slug="enrichment")
    await _make_proposal(db_session, tenant_id=tenant, aggregate_id=person, agent_slug="dedup", event_type="dedup.propose_merge")

    redis = fakeredis.aioredis.FakeRedis()
    try:
        out = await list_pending_proposals(
            db=db_session, redis=redis, caller_tenant_id=tenant,
            payload=ListPendingProposalsInput(
                status="proposed", agent_slugs=["dedup"], limit=10
            ),
        )
    finally:
        await redis.aclose()
    assert len(out.proposals) == 1
    assert out.proposals[0].agent_id == "dedup"
