from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class UnmergeResult:
    alias_id: UUID
    survivor_id: UUID
    facts_restored: int = 0
    edges_restored: int = 0
    prov_activity_id: UUID | None = None


async def unmerge(
    person_alias_id: UUID,
    *,
    db: AsyncSession,
    audit_db: AsyncSession,
    agent_slug: str = "dedup",
    retention_days: int = 90,
) -> UnmergeResult:
    """Reverse a merge: restore alias, reverse fact promotions.

    1. Load the person_alias row
    2. Verify can_unmerge = true AND merge is within retention window
    3. Restore alias's merge_status = 'canonical' and merged_into_id = NULL
    4. Reverse fact promotions: mark promoted facts back to alias
    5. Delete the person_alias row
    6. Write prov_activities of type merge_unmerge
    7. Emit dedup.merge_reverted action_event
    8. Enqueue FalkorDB edge deletion via event_outbox
    """
    alias_info = await _verify_can_unmerge(
        person_alias_id,
        db=db,
        retention_days=retention_days,
    )
    alias_id = UUID(alias_info["alias_id"])
    survivor_id = UUID(alias_info["survivor_id"])
    tenant_id = UUID(alias_info["tenant_id"])

    prov_activity_id = await _write_unmerge_prov_activity(
        alias_id=alias_id,
        survivor_id=survivor_id,
        tenant_id=tenant_id,
        audit_db=audit_db,
        agent_slug=agent_slug,
        agent_version=alias_info.get("agent_version", "0.0.0"),
    )

    await db.execute(
        text("""
            UPDATE persons
            SET merge_status = 'canonical', merged_into_id = NULL
            WHERE id = CAST(:alias_id AS uuid)
        """),
        {"alias_id": str(alias_id)},
    )

    facts_restored = await _reverse_fact_promotions(
        alias_id=alias_id,
        survivor_id=survivor_id,
        tenant_id=tenant_id,
        db=db,
        audit_db=audit_db,
        prov_activity_id=prov_activity_id,
    )

    await db.execute(
        text("""
            DELETE FROM person_alias
            WHERE alias_id = CAST(:alias_id AS uuid)
        """),
        {"alias_id": str(alias_id)},
    )

    event_id = await _emit_revert_action_event(
        alias_id=alias_id,
        survivor_id=survivor_id,
        tenant_id=tenant_id,
        db=db,
        agent_slug=agent_slug,
        facts_restored=facts_restored,
    )

    await db.execute(
        text("""
            UPDATE merge_history
            SET reverted_at = now(), reverted_event_id = CAST(:event_id AS uuid)
            WHERE removed_id = CAST(:alias_id AS uuid)
              AND reverted_at IS NULL
        """),
        {
            "event_id": str(event_id),
            "alias_id": str(alias_id),
        },
    )

    await _enqueue_graph_sync_reversal(
        alias_id=alias_id,
        survivor_id=survivor_id,
        tenant_id=tenant_id,
        db=db,
    )

    await db.flush()
    await audit_db.flush()

    return UnmergeResult(
        alias_id=alias_id,
        survivor_id=survivor_id,
        facts_restored=facts_restored,
        prov_activity_id=prov_activity_id,
    )


async def _verify_can_unmerge(
    alias_id: UUID,
    *,
    db: AsyncSession,
    retention_days: int,
) -> dict[str, Any]:
    """Load and verify the alias row is eligible for unmerge.

    Returns the alias row data.
    Raises ValueError if:
    - alias row not found
    - can_unmerge is false
    - merge is outside retention window
    """
    result = await db.execute(
        text("""
            SELECT
                pa.alias_id,
                pa.current_canonical_id AS survivor_id,
                p.canonical_owner_tenant_id AS tenant_id,
                mh.can_unmerge,
                mh.reversible_until,
                mh.performed_at
            FROM person_alias pa
            JOIN persons p ON p.id = pa.alias_id
            JOIN merge_history mh ON mh.removed_id = pa.alias_id
            WHERE pa.alias_id = CAST(:alias_id AS uuid)
        """),
        {"alias_id": str(alias_id)},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ValueError(
            f"Alias {alias_id} not found in person_alias or merge_history"
        )

    if not row["can_unmerge"]:
        raise ValueError(f"Alias {alias_id} is not eligible for unmerge (can_unmerge is false)")

    reversible_until = row["reversible_until"]
    performed_at = row["performed_at"]

    within_window: bool = False
    if reversible_until is not None:
        within_window = reversible_until > datetime.now(timezone.utc)
    elif performed_at is not None:
        deadline = performed_at + timedelta(days=retention_days)
        within_window = deadline > datetime.now(timezone.utc)

    if not within_window:
        raise ValueError(
            f"Alias {alias_id} merge is outside the retention window "
            f"({retention_days} days)"
        )

    return {
        "alias_id": str(row["alias_id"]),
        "survivor_id": str(row["survivor_id"]),
        "tenant_id": str(row["tenant_id"]),
    }


async def _reverse_fact_promotions(
    *,
    alias_id: UUID,
    survivor_id: UUID,
    tenant_id: UUID,
    db: AsyncSession,
    audit_db: AsyncSession,
    prov_activity_id: UUID,
) -> int:
    """Reverse promoted facts: for each fact that was promoted from alias
    to survivor, mark it as no longer promoted and ensure the alias's
    original facts are active.

    Query facts where was_revision_of IS NOT NULL and the original fact
    belongs to the alias. The original facts were never destroyed, just
    superseded. Reactivate them by NULLing superseded_by_fact_id.
    """
    fetch = await db.execute(
        text("""
            SELECT f.id AS promoted_fact_id, f.was_revision_of AS original_fact_id
            FROM facts f
            WHERE f.subject_id = CAST(:survivor_id AS uuid)
              AND f.was_revision_of IS NOT NULL
              AND f.was_revision_of IN (
                  SELECT id FROM facts
                  WHERE subject_id = CAST(:alias_id AS uuid)
              )
        """),
        {
            "survivor_id": str(survivor_id),
            "alias_id": str(alias_id),
        },
    )
    promotions = fetch.mappings().all()

    if not promotions:
        return 0

    original_ids = [str(row["original_fact_id"]) for row in promotions]
    promoted_ids = [str(row["promoted_fact_id"]) for row in promotions]

    original_fact_ids_list_literal = "{" + ",".join(original_ids) + "}"

    await db.execute(
        text("""
            UPDATE facts
            SET superseded_by_fact_id = NULL
            WHERE id = ANY(:original_ids::uuid[])
        """),
        {"original_ids": original_fact_ids_list_literal},
    )

    promoted_ids_literal = "{" + ",".join(promoted_ids) + "}"
    await db.execute(
        text("""
            UPDATE facts
            SET valid_until = now(),
                superseded_by_fact_id = CAST(:prov_activity_id AS uuid)
            WHERE id = ANY(:promoted_ids::uuid[])
        """),
        {
            "prov_activity_id": str(prov_activity_id),
            "promoted_ids": promoted_ids_literal,
        },
    )

    return len(promotions)


async def _write_unmerge_prov_activity(
    *,
    alias_id: UUID,
    survivor_id: UUID,
    tenant_id: UUID,
    audit_db: AsyncSession,
    agent_slug: str,
    agent_version: str,
) -> UUID:
    """Write a prov_activities row of type 'merge_unmerge'."""
    result = await audit_db.execute(
        text("""
            INSERT INTO prov_activities (
                tenant_id, activity_type, agent_id, agent_version,
                started_at, ended_at, prompt, response,
                reasoning, confidence, inputs, outputs
            ) VALUES (
                CAST(:tenant_id AS uuid),
                :activity_type,
                :agent_id,
                :agent_version,
                now(), now(),
                CAST(:prompt AS jsonb),
                CAST(:response AS jsonb),
                :reasoning,
                1.0,
                CAST(:inputs AS uuid[]),
                CAST(:outputs AS uuid[])
            )
            RETURNING id
        """),
        {
            "tenant_id": str(tenant_id),
            "activity_type": "merge_unmerge",
            "agent_id": agent_slug,
            "agent_version": agent_version,
            "prompt": json.dumps({
                "alias_id": str(alias_id),
                "survivor_id": str(survivor_id),
            }),
            "response": json.dumps({
                "status": "completed",
                "alias_id": str(alias_id),
                "survivor_id": str(survivor_id),
            }),
            "reasoning": f"Unmerge alias {alias_id} from survivor {survivor_id}",
            "inputs": "{" + str(alias_id) + "," + str(survivor_id) + "}",
            "outputs": "{" + str(alias_id) + "}",
        },
    )
    row = result.mappings().one()
    return UUID(str(row["id"]))


async def _emit_revert_action_event(
    *,
    alias_id: UUID,
    survivor_id: UUID,
    tenant_id: UUID,
    db: AsyncSession,
    agent_slug: str,
    facts_restored: int,
) -> UUID:
    """Insert an action_event of type dedup.merge_reverted."""
    now_iso = datetime.now(timezone.utc).isoformat()
    result = await db.execute(
        text("""
            INSERT INTO action_event (
                event_type, event_version, tenant_id,
                aggregate_type, aggregate_id, affected_ids,
                payload, actor, actor_type,
                status, applied_at,
                reversibility_class, agent_version
            ) VALUES (
                :event_type, 1, CAST(:tenant_id AS uuid),
                :aggregate_type, CAST(:aggregate_id AS uuid),
                CAST(:affected_ids AS uuid[]),
                CAST(:payload AS jsonb),
                CAST(:actor AS jsonb),
                :actor_type,
                :status, now(),
                :reversibility_class, :agent_version
            )
            RETURNING event_id
        """),
        {
            "event_type": "dedup.merge_reverted",
            "tenant_id": str(tenant_id),
            "aggregate_type": "person",
            "aggregate_id": str(alias_id),
            "affected_ids": "{" + str(alias_id) + "," + str(survivor_id) + "}",
            "payload": json.dumps({
                "alias_id": str(alias_id),
                "survivor_id": str(survivor_id),
                "facts_restored": facts_restored,
                "reverted_at": now_iso,
            }),
            "actor": json.dumps({"agent_slug": agent_slug}),
            "actor_type": "agent",
            "status": "applied",
            "reversibility_class": "irreversible",
            "agent_version": "0.0.0",
        },
    )
    row = result.mappings().one()
    return UUID(str(row["event_id"]))


async def _enqueue_graph_sync_reversal(
    *,
    alias_id: UUID,
    survivor_id: UUID,
    tenant_id: UUID,
    db: AsyncSession,
) -> None:
    """Enqueue a FalkorDB edge deletion via event_outbox."""
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
            "channel": "falkordb.edge.delete",
            "tenant_id": str(tenant_id),
            "payload": json.dumps({
                "alias_id": str(alias_id),
                "survivor_id": str(survivor_id),
                "edge_label": "dedup:alias_of",
            }),
        },
    )
