from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents.dedup.hipaa_fence import PersonRef, crosses_hipaa_fence


@dataclass
class MergeResult:
    merge_history_id: UUID
    survivor_id: UUID
    alias_id: UUID
    can_unmerge: bool = True
    can_unmerge_until: datetime | None = None
    facts_promoted: int = 0
    facts_added_to_survivor: list[str] = field(default_factory=list)


async def apply_merge(
    action_event_id: UUID,
    *,
    db: AsyncSession,
    audit_db: AsyncSession,
    agent_slug: str = "dedup",
    survivor_override: UUID | None = None,
    retention_days: int = 90,
) -> MergeResult:
    """Apply a propose_merge action: survivor-link write + fact promotion.

    1. Load the action_event row and verify it is a dedup.propose_merge proposal
    2. Extract person_a_id and person_b_id from the evidence pack
    3. Determine survivor (default: older record by created_at)
    4. Check HIPAA fence
    5. Create person_alias row
    6. Write prov_activities of type merge_approve (needed for FK below)
    7. Update merge_history with merge_activity_id
    8. Promote facts
    9. Update alias merge_status = 'merged', merged_into_id = survivor_id
    10. Emit dedup.merge_applied action_event
    11. Enqueue FalkorDB graph sync
    """
    # 1. Load the action_event row
    ev_result = await audit_db.execute(
        text("""
            SELECT event_id, event_type, tenant_id, aggregate_type,
                   aggregate_id, payload, evidence, confidence, actor,
                   rationale, decision_payload
            FROM action_event
            WHERE event_id = CAST(:event_id AS uuid)
        """),
        {"event_id": str(action_event_id)},
    )
    ev_row = ev_result.mappings().first()
    if ev_row is None:
        raise ValueError(f"action_event {action_event_id} not found")

    event_type: str = ev_row["event_type"]
    if event_type != "dedup.propose_merge":
        raise ValueError(f"expected dedup.propose_merge, got {event_type}")

    tenant_id = UUID(str(ev_row["tenant_id"]))
    evidence: dict[str, Any] = ev_row["evidence"]

    # 2. Extract person_a_id and person_b_id from evidence
    person_a_id = UUID(str(evidence["person_a_id"]))
    person_b_id = UUID(str(evidence["person_b_id"]))

    # 3. Determine survivor (default: older record by created_at)
    a_row = await _load_person(person_a_id, db=db)
    b_row = await _load_person(person_b_id, db=db)

    if a_row is None or b_row is None:
        raise ValueError(
            f"person not found: one of {person_a_id}, {person_b_id}"
        )

    if survivor_override is not None:
        if survivor_override == person_a_id:
            survivor_id = person_a_id
            alias_id = person_b_id
        elif survivor_override == person_b_id:
            survivor_id = person_b_id
            alias_id = person_a_id
        else:
            raise ValueError(
                f"survivor_override {survivor_override} not in merge pair"
            )
    else:
        if a_row["created_at"] <= b_row["created_at"]:
            survivor_id = person_a_id
            alias_id = person_b_id
        else:
            survivor_id = person_b_id
            alias_id = person_a_id

    # 4. Check HIPAA fence
    survivor_ref = PersonRef(survivor_id, tenant_id)
    alias_ref = PersonRef(alias_id, tenant_id)
    if await crosses_hipaa_fence(survivor_ref, alias_ref):
        raise ValueError(
            "HIPAA fence violation: cannot merge cross-tenant persons"
        )

    merge_history_id = UUID(str(evidence.get("merge_history_id", "")))

    actor_chain: dict[str, Any] = {
        "sub": agent_slug,
        "agent_version": "1.0.0",
        "act": {"sub": "system"},
    }

    # 5. Create person_alias row
    await db.execute(
        text("""
            INSERT INTO person_alias (alias_id, current_canonical_id, merged_at_event_id)
            VALUES (CAST(:alias_id AS uuid), CAST(:survivor_id AS uuid), CAST(:event_id AS uuid))
        """),
        {
            "alias_id": str(alias_id),
            "survivor_id": str(survivor_id),
            "event_id": str(action_event_id),
        },
    )

    # 6. Write prov_activities before updating merge_history (FK reference)
    prov_activity_id = await _write_prov_activity(
        alias_id=alias_id,
        survivor_id=survivor_id,
        tenant_id=tenant_id,
        audit_db=audit_db,
        action_event_id=action_event_id,
        merge_confidence=float(ev_row.get("confidence") or 0.0),
        agent_version=actor_chain["agent_version"],
    )

    # 7. Update merge_history with merge_activity_id
    reversible_until: datetime | None = None
    if retention_days > 0:
        reversible_until = datetime.now(timezone.utc) + timedelta(
            days=retention_days
        )

    await db.execute(
        text("""
            UPDATE merge_history
            SET merge_activity_id = CAST(:prov_activity_id AS uuid),
                reversible_until = :reversible_until,
                can_unmerge = TRUE
            WHERE id = CAST(:id AS uuid)
        """),
        {
            "prov_activity_id": str(prov_activity_id),
            "reversible_until": reversible_until,
            "id": str(merge_history_id),
        },
    )

    # 8. Promote facts from alias to survivor
    facts_promoted = await _promote_facts(
        alias_id=alias_id,
        survivor_id=survivor_id,
        tenant_id=tenant_id,
        db=db,
        audit_db=audit_db,
        merge_history_id=merge_history_id,
        prov_activity_id=prov_activity_id,
        actor_chain=actor_chain,
    )

    # 9. Update alias merge_status and merged_into_id
    await db.execute(
        text("""
            UPDATE persons
            SET merge_status = CAST(:merge_status AS merge_status),
                merged_into_id = CAST(:survivor_id AS uuid)
            WHERE id = CAST(:alias_id AS uuid)
        """),
        {
            "merge_status": "merged_into",
            "survivor_id": str(survivor_id),
            "alias_id": str(alias_id),
        },
    )

    # 10. Emit dedup.merge_applied action_event
    applied_payload: dict[str, Any] = {
        "merge_history_id": str(merge_history_id),
        "survivor_id": str(survivor_id),
        "alias_id": str(alias_id),
        "facts_promoted": facts_promoted,
        "prov_activity_id": str(prov_activity_id),
        "reversible_until": reversible_until.isoformat()
        if reversible_until
        else None,
    }

    now_iso = datetime.now(timezone.utc).isoformat()
    affected_ids_literal = (
        "{" + str(alias_id) + "," + str(survivor_id) + "}"
    )

    await audit_db.execute(
        text("""
            INSERT INTO action_event (
                event_type, event_version, tenant_id,
                aggregate_type, aggregate_id, affected_ids,
                payload, actor, actor_type,
                confidence, evidence, rationale,
                status, applied_at,
                reversibility_class, agent_version
            ) VALUES (
                :event_type, 1, CAST(:tenant_id AS uuid),
                :aggregate_type, CAST(:aggregate_id AS uuid),
                CAST(:affected_ids AS uuid[]),
                CAST(:payload AS jsonb),
                CAST(:actor AS jsonb),
                :actor_type,
                :confidence, CAST(:evidence AS jsonb), :rationale,
                :status, now(),
                :reversibility_class, :agent_version
            )
        """),
        {
            "event_type": "dedup.merge_applied",
            "tenant_id": str(tenant_id),
            "aggregate_type": "person",
            "aggregate_id": str(survivor_id),
            "affected_ids": affected_ids_literal,
            "payload": json.dumps(applied_payload, sort_keys=True, default=str),
            "actor": json.dumps(actor_chain, sort_keys=True),
            "actor_type": "agent",
            "confidence": float(ev_row.get("confidence") or 0.0),
            "evidence": json.dumps(evidence, sort_keys=True, default=str),
            "rationale": (
                f"Merge applied: alias {alias_id} merged into survivor {survivor_id}"
            ),
            "status": "applied",
            "reversibility_class": "reversible_24h",
            "agent_version": actor_chain["agent_version"],
        },
    )

    # 11. Enqueue FalkorDB graph sync
    await _enqueue_graph_sync(
        alias_id=alias_id,
        survivor_id=survivor_id,
        tenant_id=tenant_id,
        db=db,
    )

    return MergeResult(
        merge_history_id=merge_history_id,
        survivor_id=survivor_id,
        alias_id=alias_id,
        can_unmerge=True,
        can_unmerge_until=reversible_until,
        facts_promoted=facts_promoted,
    )


async def _load_person(
    person_id: UUID,
    *,
    db: AsyncSession,
) -> dict[str, Any] | None:
    """Load a person record by ID."""
    result = await db.execute(
        text("""
            SELECT id, tenant_id, merge_status, merged_into_id,
                   created_at, updated_at, canonical_owner_tenant_id
            FROM persons
            WHERE id = CAST(:person_id AS uuid)
        """),
        {"person_id": str(person_id)},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def _promote_facts(
    *,
    alias_id: UUID,
    survivor_id: UUID,
    tenant_id: UUID,
    db: AsyncSession,
    audit_db: AsyncSession,
    merge_history_id: UUID,
    prov_activity_id: UUID,
    actor_chain: dict[str, Any],
) -> int:
    """Copy alias facts into survivor's record.

    For each fact on alias that does not already exist on survivor
    (same predicate + same object_value), copy it with:
    - was_revision_of pointing to the original fact
    - prov_activity_id pointing to the merge activity
    - The original fact marked with superseded_by_fact_id pointing to
      the new copy and promoted_into_survivor_id set to the survivor.
    """
    fetch = await db.execute(
        text("""
            SELECT id, tenant_id, subject_kind, subject_id,
                   predicate, object_kind, object_value, object_ref_id,
                   source_id, confidence, observed_at,
                   valid_from, valid_until, superseded_by_fact_id,
                   human_verified, verified_by_actor_id, verified_at
            FROM facts
            WHERE subject_id = CAST(:alias_id AS uuid)
              AND subject_kind = 'person'
        """),
        {"alias_id": str(alias_id)},
    )
    rows = fetch.mappings().all()

    promoted = 0
    for row in rows:
        fact_id = UUID(str(row["id"]))
        predicate: str = row["predicate"]
        object_value: dict[str, Any] = row["object_value"]

        existing = await db.execute(
            text("""
                SELECT 1
                FROM facts
                WHERE subject_id = CAST(:survivor_id AS uuid)
                  AND subject_kind = 'person'
                  AND predicate = :predicate
                  AND object_value = CAST(:object_value AS jsonb)
                LIMIT 1
            """),
            {
                "survivor_id": str(survivor_id),
                "predicate": predicate,
                "object_value": json.dumps(object_value, default=str),
            },
        )
        if existing.mappings().first() is not None:
            continue

        new_fact_id = uuid4()
        await db.execute(
            text("""
                INSERT INTO facts (
                    id, tenant_id, subject_kind, subject_id,
                    predicate, object_kind, object_value, object_ref_id,
                    source_id, confidence, observed_at,
                    valid_from, valid_until,
                    human_verified, verified_by_actor_id, verified_at,
                    prov_activity_id, was_revision_of
                ) VALUES (
                    CAST(:id AS uuid),
                    CAST(:tenant_id AS uuid),
                    :subject_kind,
                    CAST(:subject_id AS uuid),
                    :predicate, :object_kind,
                    CAST(:object_value AS jsonb),
                    CAST(:object_ref_id AS uuid),
                    CAST(:source_id AS uuid),
                    :confidence, :observed_at,
                    :valid_from, :valid_until,
                    :human_verified,
                    CAST(:verified_by_actor_id AS uuid),
                    :verified_at,
                    CAST(:prov_activity_id AS uuid),
                    CAST(:was_revision_of AS uuid)
                )
            """),
            {
                "id": str(new_fact_id),
                "tenant_id": str(tenant_id),
                "subject_kind": "person",
                "subject_id": str(survivor_id),
                "predicate": predicate,
                "object_kind": row["object_kind"],
                "object_value": json.dumps(object_value, default=str),
                "object_ref_id": str(row["object_ref_id"])
                if row["object_ref_id"]
                else None,
                "source_id": str(row["source_id"]),
                "confidence": float(row["confidence"])
                if row["confidence"] is not None
                else None,
                "observed_at": row["observed_at"],
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "human_verified": bool(row["human_verified"]),
                "verified_by_actor_id": str(row["verified_by_actor_id"])
                if row["verified_by_actor_id"]
                else None,
                "verified_at": row["verified_at"],
                "prov_activity_id": str(prov_activity_id),
                "was_revision_of": str(fact_id),
            },
        )

        await db.execute(
            text("""
                UPDATE facts
                SET superseded_by_fact_id = CAST(:new_fact_id AS uuid),
                    promoted_into_survivor_id = CAST(:survivor_id AS uuid)
                WHERE id = CAST(:fact_id AS uuid)
            """),
            {
                "new_fact_id": str(new_fact_id),
                "survivor_id": str(survivor_id),
                "fact_id": str(fact_id),
            },
        )

        promoted += 1

    return promoted


async def _write_prov_activity(
    *,
    alias_id: UUID,
    survivor_id: UUID,
    tenant_id: UUID,
    audit_db: AsyncSession,
    action_event_id: UUID,
    merge_confidence: float,
    agent_version: str,
    trace_id: str | None = None,
    span_id: str | None = None,
) -> UUID:
    """Write a prov_activities row of type 'merge_approve'."""
    inputs_literal = "{" + str(alias_id) + "," + str(survivor_id) + "}"
    outputs_literal = "{" + str(action_event_id) + "}"

    result = await audit_db.execute(
        text("""
            INSERT INTO prov_activities (
                tenant_id, activity_type, agent_id, agent_version,
                started_at, ended_at, prompt, response,
                reasoning, confidence, inputs, outputs, trace_id
            ) VALUES (
                CAST(:tenant_id AS uuid),
                :activity_type,
                :agent_id,
                :agent_version,
                now(), now(),
                CAST(:prompt AS jsonb),
                CAST(:response AS jsonb),
                :reasoning,
                :confidence,
                CAST(:inputs AS uuid[]),
                CAST(:outputs AS uuid[]),
                :trace_id
            )
            RETURNING id
        """),
        {
            "tenant_id": str(tenant_id),
            "activity_type": "merge_approve",
            "agent_id": "dedup",
            "agent_version": agent_version,
            "prompt": json.dumps({
                "action_event_id": str(action_event_id),
                "survivor_id": str(survivor_id),
                "alias_id": str(alias_id),
            }),
            "response": json.dumps({
                "status": "approved",
                "alias_id": str(alias_id),
                "survivor_id": str(survivor_id),
            }),
            "reasoning": "Merge approved via dedup.propose_merge action_event",
            "confidence": merge_confidence,
            "inputs": inputs_literal,
            "outputs": outputs_literal,
            "trace_id": trace_id,
        },
    )
    row = result.mappings().one()
    return UUID(str(row["id"]))


async def _enqueue_graph_sync(
    *,
    alias_id: UUID,
    survivor_id: UUID,
    tenant_id: UUID,
    db: AsyncSession,
) -> None:
    """Enqueue a FalkorDB graph sync via event_outbox."""
    await db.execute(
        text("""
            INSERT INTO event_outbox (channel, tenant_id, payload)
            VALUES (
                :channel,
                CAST(:tenant_id AS uuid),
                CAST(:payload AS jsonb)
            )
        """),
        {
            "channel": "falkordb.edge.create",
            "tenant_id": str(tenant_id),
            "payload": json.dumps({
                "type": "merge_applied",
                "alias_id": str(alias_id),
                "survivor_id": str(survivor_id),
                "edge_label": "dedup:alias_of",
            }),
        },
    )
