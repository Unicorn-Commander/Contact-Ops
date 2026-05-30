from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def emit_agent_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    agent_slug: str,
    payload_before: dict[str, Any] | None,
    payload_after: dict[str, Any],
    affected_ids: list[uuid.UUID] | None = None,
    confidence: float | None = None,
    status: str = "applied",
    rationale: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> uuid.UUID:
    payload = {"before": payload_before, "after": payload_after}
    payload_bytes = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    prev_hash = await db.scalar(
        text(
            """
            SELECT content_hash
            FROM action_event
            WHERE tenant_id = CAST(:tenant_id AS uuid)
            ORDER BY proposed_at DESC
            LIMIT 1
            """
        ),
        {"tenant_id": str(tenant_id)},
    )
    row = await db.execute(
        text(
            """
            INSERT INTO action_event (
                tenant_id, event_type, aggregate_type, aggregate_id, affected_ids,
                payload, actor, actor_type, human_authority, confidence, evidence,
                rationale, status, content_hash, prev_event_hash
            ) VALUES (
                CAST(:tenant_id AS uuid), :event_type, CAST(:aggregate_type AS entity_kind),
                CAST(:aggregate_id AS uuid), CAST(:affected_ids AS uuid[]),
                CAST(:payload AS jsonb), CAST(:actor AS jsonb), 'agent',
                NULL, :confidence, CAST(:evidence AS jsonb), :rationale,
                CAST(:status AS event_status), :content_hash, :prev_hash
            )
            RETURNING event_id
            """
        ),
        {
            "tenant_id": str(tenant_id),
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": str(aggregate_id),
            "affected_ids": [str(item) for item in affected_ids or []],
            "payload": json.dumps(payload, sort_keys=True, default=str),
            "actor": json.dumps({"agent": agent_slug}),
            "confidence": confidence,
            "evidence": json.dumps(evidence or {}),
            "rationale": rationale,
            "status": status,
            "content_hash": hashlib.sha256(payload_bytes).digest(),
            "prev_hash": prev_hash,
        },
    )
    return uuid.UUID(str(row.scalar_one()))


def payload_after(row: dict[str, Any]) -> dict[str, Any]:
    decision_payload = row.get("decision_payload") or {}
    if isinstance(decision_payload, str):
        decision_payload = json.loads(decision_payload)
    if isinstance(decision_payload, dict) and isinstance(
        decision_payload.get("payload_after"),
        dict,
    ):
        return dict(decision_payload["payload_after"])
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict) and isinstance(payload.get("after"), dict):
        return dict(payload["after"])
    if isinstance(payload, dict):
        return dict(payload)
    return {}
