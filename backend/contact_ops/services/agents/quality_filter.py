from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.services.agents._events import emit_agent_event, payload_after

AGENT_SLUG = "quality-filter-agent"
GENERIC_NAMES = {
    "customer",
    "support",
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "unknown",
}
HUMAN_SOURCES = {
    "address_book",
    "contacts",
    "icloud",
    "gmail",
    "m365",
    "google_contacts",
    "ios_contacts_export",
}


async def run_quality_filter(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    dry_run: bool = False,
) -> dict[str, Any]:
    rows = (
        await db.execute(
            text(
                """
                SELECT event_id, aggregate_id, payload, decision_payload
                FROM action_event
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND event_type = 'person.create'
                  AND status = 'proposed'
                ORDER BY proposed_at ASC
                """
            ),
            {"tenant_id": str(tenant_id)},
        )
    ).mappings().all()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        payload = payload_after(dict(row))
        reason = _archive_reason(payload)
        if reason is not None:
            candidates.append(
                {
                    "event_id": str(row["event_id"]),
                    "aggregate_id": str(row["aggregate_id"]),
                    "reason": reason,
                    "display_name": payload.get("display_name"),
                }
            )
    if dry_run or not candidates:
        return {"dry_run": dry_run, "archived_count": 0, "candidates": candidates}

    for candidate in candidates:
        await db.execute(
            text(
                """
                UPDATE action_event
                SET status = 'resolved',
                    decision_payload = CAST(:decision_payload AS jsonb)
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND event_id = CAST(:event_id AS uuid)
                """
            ),
            {
                "tenant_id": str(tenant_id),
                "event_id": candidate["event_id"],
                "decision_payload": json.dumps(
                    {"action": "auto_archived", "reason": candidate["reason"]},
                    sort_keys=True,
                ),
            },
        )
        await emit_agent_event(
            db,
            tenant_id=tenant_id,
            event_type="quality.filter.archived",
            aggregate_type="person",
            aggregate_id=uuid.UUID(candidate["aggregate_id"]),
            agent_slug=AGENT_SLUG,
            affected_ids=[uuid.UUID(candidate["event_id"])],
            payload_before={"proposal_id": candidate["event_id"]},
            payload_after={"action": "auto_archived", "reason": candidate["reason"]},
            rationale="Auto-archived low-quality connector noise.",
        )
    return {"dry_run": False, "archived_count": len(candidates), "candidates": candidates}


def _archive_reason(payload: dict[str, Any]) -> str | None:
    display_name = str(payload.get("display_name") or "").strip()
    emails = payload.get("emails") if isinstance(payload.get("emails"), list) else []
    phones = payload.get("phones") if isinstance(payload.get("phones"), list) else []
    if not display_name and not emails and not phones:
        return "empty_identity"
    if display_name.casefold() in GENERIC_NAMES and not _has_human_source(payload):
        return "generic_placeholder"
    return None


def _has_human_source(payload: dict[str, Any]) -> bool:
    sources = payload.get("sources")
    if not isinstance(sources, list):
        sources = [payload.get("source")] if payload.get("source") else []
    for source in sources:
        if not isinstance(source, dict):
            continue
        provider = str(source.get("provider") or source.get("source") or "").casefold()
        action = str(source.get("action") or "").casefold()
        if provider in HUMAN_SOURCES and "inference" not in action:
            return True
    return False
