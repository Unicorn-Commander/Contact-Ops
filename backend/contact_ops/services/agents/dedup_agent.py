from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.services.agents._events import emit_agent_event, payload_after

AGENT_SLUG = "dedup-agent"


async def run_dedup_agent(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    since: datetime | None = None,
) -> dict[str, Any]:
    if since is not None:
        rows = (
            await db.execute(
                text(
                    """
                SELECT event_id, aggregate_id, payload, decision_payload, confidence
                FROM action_event
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND event_type = 'person.create'
                  AND status = 'proposed'
                  AND proposed_at >= :since
                ORDER BY proposed_at ASC
                """
                ),
                {"tenant_id": str(tenant_id), "since": since},
            )
        ).mappings().all()
    else:
        rows = (
            await db.execute(
                text(
                    """
                SELECT event_id, aggregate_id, payload, decision_payload, confidence
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
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        payload = payload_after(dict(row))
        clusters[_fingerprint(payload)].append({**dict(row), "_payload_after": payload})

    merged_clusters = 0
    merged_proposals = 0
    canonical_ids: list[str] = []
    for cluster in clusters.values():
        if len(cluster) < 2:
            continue
        canonical = max(cluster, key=lambda item: _populated_score(item["_payload_after"]))
        others = [item for item in cluster if item["event_id"] != canonical["event_id"]]
        merged_payload = dict(canonical["_payload_after"])
        for other in others:
            merged_payload = _merge_payloads(merged_payload, other["_payload_after"])
        await db.execute(
            text(
                """
                UPDATE action_event
                SET payload = jsonb_set(payload, '{after}', CAST(:payload_after AS jsonb), true),
                    decision_payload = CAST(:decision_payload AS jsonb)
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND event_id = CAST(:event_id AS uuid)
                """
            ),
            {
                "tenant_id": str(tenant_id),
                "event_id": str(canonical["event_id"]),
                "payload_after": json.dumps(merged_payload, sort_keys=True, default=str),
                "decision_payload": json.dumps(
                    {"payload_before": None, "payload_after": merged_payload},
                    sort_keys=True,
                    default=str,
                ),
            },
        )
        await db.execute(
            text(
                """
                UPDATE action_event
                SET status = 'resolved',
                    decision_payload = CAST(:decision_payload AS jsonb)
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND event_id = ANY(CAST(:event_ids AS uuid[]))
                """
            ),
            {
                "tenant_id": str(tenant_id),
                "event_ids": [str(item["event_id"]) for item in others],
                "decision_payload": json.dumps(
                    {
                        "action": "merged_into",
                        "canonical_event_id": str(canonical["event_id"]),
                    },
                    sort_keys=True,
                ),
            },
        )
        avg_confidence = sum(float(item["confidence"] or 0) for item in cluster) / len(cluster)
        await emit_agent_event(
            db,
            tenant_id=tenant_id,
            event_type="dedup.cluster.merged",
            aggregate_type="person",
            aggregate_id=uuid.UUID(str(canonical["aggregate_id"])),
            agent_slug=AGENT_SLUG,
            affected_ids=[uuid.UUID(str(item["event_id"])) for item in cluster],
            payload_before={"cluster_event_ids": [str(item["event_id"]) for item in cluster]},
            payload_after={
                "canonical_event_id": str(canonical["event_id"]),
                "merged_event_ids": [str(item["event_id"]) for item in others],
                "actor_chain": {"agent": AGENT_SLUG, "cluster_size": len(cluster)},
            },
            confidence=avg_confidence,
            rationale="Merged duplicate person.create proposals into the canonical proposal.",
        )
        merged_clusters += 1
        merged_proposals += len(others)
        canonical_ids.append(str(canonical["event_id"]))
    return {
        "clusters_merged": merged_clusters,
        "proposals_merged": merged_proposals,
        "canonical_event_ids": canonical_ids,
    }


def _fingerprint(payload: dict[str, Any]) -> str:
    emails = sorted(
        str(item.get("address") or item.get("email") or "").strip().lower()
        for item in _list_of_dicts(payload.get("emails"))
        if item.get("address") or item.get("email")
    )
    phones = sorted(
        _normalize_phone(str(item.get("e164") or item.get("number") or ""))
        for item in _list_of_dicts(payload.get("phones"))
        if item.get("e164") or item.get("number")
    )
    return json.dumps(
        {
            "display_name": str(payload.get("display_name") or "").strip().lower(),
            "emails": emails,
            "phones": phones,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _merge_payloads(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if key in {"emails", "phones", "addresses", "tags", "sources"}:
            merged[key] = _union(merged.get(key), value)
        elif key == "source":
            merged["sources"] = _union(merged.get("sources"), [merged.get("source"), value])
        elif not merged.get(key) and value:
            merged[key] = value
    return merged


def _union(left: Any, right: Any) -> list[Any]:
    items: list[Any] = []
    for value in (left if isinstance(left, list) else [left] if left else []):
        items.append(value)
    for value in (right if isinstance(right, list) else [right] if right else []):
        marker = json.dumps(value, sort_keys=True, default=str)
        if marker not in {json.dumps(item, sort_keys=True, default=str) for item in items}:
            items.append(value)
    return items


def _populated_score(payload: dict[str, Any]) -> int:
    score = sum(1 for value in payload.values() if value not in (None, "", [], {}))
    score += sum(len(payload.get(key) or []) for key in ("emails", "phones", "addresses", "tags"))
    return score


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _normalize_phone(value: str) -> str:
    if value.startswith("+"):
        return "+" + re.sub(r"\D+", "", value)
    digits = re.sub(r"\D+", "", value)
    return f"+1{digits}" if len(digits) == 10 else digits
