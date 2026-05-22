"""get_proposal_evidence — assemble the EvidencePanel payload.

Joins ``action_event`` to ``prov_activities`` via ``evidence_pack_id``
(or ``trace_id`` as fallback) and collects the per-aggregate source
events in the last 30 days. Per Aaron's B7:

* ``evidence_pack_id`` -> prov_activities.id (FK added in migration 0024)
* ``source_events``    -> action_event WHERE aggregate_id = ? AND
                           proposed_at > now() - 30 days
* ``reasoning``        -> prov_activities.reasoning, fallback to
                           action_event.rationale
* ``laminar_trace_url`` -> ``f"{LAMINAR_BASE_URL}/traces/{trace_id}"``
* cost/tokens          -> prov_activities (JOIN by trace_id when
                           evidence_pack_id is absent)

Source-event titles are derived from the event_type taxonomy via the
``_event_type_title`` helper. Deep links route into the existing
/people/:id and /orgs/:id detail pages of the admin UI.
"""
# ruff: noqa: E501

from __future__ import annotations

import os
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.schemas.inbox import ProposalEvidenceOutput, SourceEvent

DEFAULT_LAMINAR_BASE = "https://laminar.unicorncommander.net"


def _event_type_title(event_type: str) -> str:
    """Cheap, deterministic readable title for an event_type."""
    if not event_type:
        return "(unknown event)"
    parts = event_type.split(".")
    if len(parts) >= 2:
        verb = parts[-1].replace("_", " ")
        kind = parts[0]
        return f"{kind}: {verb}"
    return event_type.replace("_", " ")


def _deep_link_for(aggregate_type: str, aggregate_id: uuid.UUID) -> str:
    if aggregate_type == "person":
        return f"/people/{aggregate_id}"
    if aggregate_type == "org":
        return f"/orgs/{aggregate_id}"
    return f"/entities/{aggregate_id}"


async def get_proposal_evidence(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    proposal_id: uuid.UUID,
) -> ProposalEvidenceOutput | None:
    """Assemble the evidence pack for a single proposal."""
    head = await db.execute(
        text(
            """
            SELECT
                ae.event_id,
                ae.aggregate_id,
                ae.aggregate_type,
                ae.evidence_pack_id,
                ae.trace_id,
                ae.rationale,
                ae.proposed_at,
                ae.tenant_id
            FROM action_event ae
            WHERE ae.event_id = CAST(:id AS uuid)
              AND ae.tenant_id = CAST(:tenant AS uuid)
            """
        ),
        {"id": str(proposal_id), "tenant": str(tenant_id)},
    )
    row = head.mappings().first()
    if row is None:
        return None

    aggregate_id = uuid.UUID(str(row["aggregate_id"]))
    evidence_pack_id = (
        uuid.UUID(str(row["evidence_pack_id"])) if row["evidence_pack_id"] else None
    )
    trace_id = row["trace_id"]
    rationale_fallback = row["rationale"] or ""

    prov_row: dict[str, Any] | None = None
    if evidence_pack_id is not None:
        prov_result = await db.execute(
            text(
                """
                SELECT id, reasoning, cost_cents, tokens_input, tokens_output,
                       trace_id
                FROM prov_activities
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": str(evidence_pack_id)},
        )
        m = prov_result.mappings().first()
        if m is not None:
            prov_row = dict(m)
    elif trace_id is not None:
        prov_result = await db.execute(
            text(
                """
                SELECT id, reasoning, cost_cents, tokens_input, tokens_output,
                       trace_id
                FROM prov_activities
                WHERE trace_id = :trace
                ORDER BY started_at DESC
                LIMIT 1
                """
            ),
            {"trace": trace_id},
        )
        m = prov_result.mappings().first()
        if m is not None:
            prov_row = dict(m)

    reasoning = (
        str(prov_row["reasoning"])
        if prov_row and prov_row.get("reasoning")
        else (rationale_fallback or None)
    )
    cost_cents = int(prov_row["cost_cents"]) if prov_row and prov_row.get("cost_cents") is not None else 0
    tokens_input = int(prov_row["tokens_input"]) if prov_row and prov_row.get("tokens_input") is not None else 0
    tokens_output = int(prov_row["tokens_output"]) if prov_row and prov_row.get("tokens_output") is not None else 0
    prov_activity_id = uuid.UUID(str(prov_row["id"])) if prov_row else None

    # source_events: same aggregate, last 30 days, excluding this proposal itself
    sources_result = await db.execute(
        text(
            """
            SELECT event_id, event_type, proposed_at, aggregate_type, aggregate_id
            FROM action_event
            WHERE aggregate_id = CAST(:agg AS uuid)
              AND tenant_id = CAST(:tenant AS uuid)
              AND event_id != CAST(:self AS uuid)
              AND proposed_at > now() - INTERVAL '30 days'
              AND status IN ('applied', 'proposed', 'approved')
            ORDER BY proposed_at DESC
            LIMIT 25
            """
        ),
        {
            "agg": str(aggregate_id),
            "tenant": str(tenant_id),
            "self": str(proposal_id),
        },
    )
    source_events: list[SourceEvent] = []
    for s in sources_result.mappings():
        source_events.append(
            SourceEvent(
                event_id=uuid.UUID(str(s["event_id"])),
                kind=s["event_type"],
                title=_event_type_title(s["event_type"]),
                occurred_at=s["proposed_at"],
                deep_link=_deep_link_for(str(s["aggregate_type"]), aggregate_id),
            )
        )

    laminar_base = os.environ.get("LAMINAR_BASE_URL", DEFAULT_LAMINAR_BASE).rstrip("/")
    laminar_trace_url = f"{laminar_base}/traces/{trace_id}" if trace_id else None

    return ProposalEvidenceOutput(
        proposal_id=proposal_id,
        source_events=source_events,
        reasoning=reasoning,
        laminar_trace_url=laminar_trace_url,
        prov_activity_id=prov_activity_id,
        cost_cents=cost_cents,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
    )


__all__ = ["get_proposal_evidence"]
