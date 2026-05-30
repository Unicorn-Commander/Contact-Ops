from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.services.agents._events import emit_agent_event
from contact_ops.services.inbox_mutations import InboxMutationError, approve_proposal

AGENT_SLUG = "confidence-approver"


async def run_confidence_approver(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    dry_run: bool = False,
) -> dict[str, Any]:
    disabled = await db.scalar(
        text("SELECT auto_approve_disabled FROM tenants WHERE id = CAST(:tenant_id AS uuid)"),
        {"tenant_id": str(tenant_id)},
    )
    if disabled:
        return {"dry_run": dry_run, "approved_count": 0, "candidates": [], "disabled": True}

    rows = (
        await db.execute(
            text(
                """
                SELECT ae.event_id, ae.aggregate_id, ae.confidence
                FROM action_event ae
                WHERE ae.tenant_id = CAST(:tenant_id AS uuid)
                  AND ae.status = 'proposed'
                  AND ae.confidence >= 0.95
                  AND ae.reversibility_class IN ('reversible', 'reversible_24h')
                  AND COALESCE(
                    (ae.decision_payload->'compliance'->>'hipaa')::boolean, false
                  ) = false
                  AND ae.parent_proposal_id IS NULL
                  AND COALESCE((ae.decision_payload->>'cross_tenant')::boolean, false) = false
                  AND NOT EXISTS (
                    SELECT 1 FROM proposal_conflict pc
                    WHERE pc.resolved_at IS NULL
                      AND (
                        pc.primary_proposal_id = ae.event_id
                        OR pc.conflicting_proposal_id = ae.event_id
                      )
                  )
                ORDER BY ae.proposed_at ASC
                """
            ),
            {"tenant_id": str(tenant_id)},
        )
    ).mappings().all()
    candidates = [
        {
            "event_id": str(row["event_id"]),
            "aggregate_id": str(row["aggregate_id"]),
            "confidence": float(row["confidence"] or 0),
        }
        for row in rows
    ]
    if dry_run:
        return {"dry_run": True, "approved_count": 0, "candidates": candidates}

    approved: list[dict[str, Any]] = []
    for candidate in candidates:
        event_id = str(candidate["event_id"])
        aggregate_id = str(candidate["aggregate_id"])
        confidence = float(cast(float, candidate["confidence"]))
        try:
            await approve_proposal(
                db=db,
                tenant_id=tenant_id,
                reviewer_id=uuid.UUID(int=0),
                proposal_id=uuid.UUID(event_id),
                tier_assigned=1,
                typed_phrase=None,
                field_choices=None,
                custom_values=None,
                time_to_decide_sec=None,
                keyboard_path=False,
                typed_phrase_used=False,
            )
        except InboxMutationError:
            continue
        await emit_agent_event(
            db,
            tenant_id=tenant_id,
            event_type="confidence_approver.applied",
            aggregate_type="person",
            aggregate_id=uuid.UUID(aggregate_id),
            agent_slug=AGENT_SLUG,
            affected_ids=[uuid.UUID(event_id)],
            payload_before={"proposal_id": event_id, "status": "proposed"},
            payload_after={
                "proposal_id": event_id,
                "status": "applied",
                "actor_chain": {"agent": AGENT_SLUG, "confidence": confidence},
            },
            confidence=confidence,
            rationale="Auto-approved high-confidence reversible proposal.",
        )
        approved.append(candidate)
    return {"dry_run": False, "approved_count": len(approved), "candidates": approved}
