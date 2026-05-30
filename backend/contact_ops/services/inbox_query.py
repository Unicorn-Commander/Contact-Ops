"""list_pending_proposals — the read side of the inbox MCP surface.

This module owns:

* Filter-to-SQL translation for the 14 filter args in
  ``ListPendingProposalsInput``
* Derived-field computation (compliance, cross_tenant, fields_changed,
  is_dedup, is_edge, is_delete, bulk_count) per Aaron's C2/C3/C4
* Personal-org cluster separation per D6 / ``feedback_personal_org_separation``
* Server-side cluster grouping per design doc §11.5 + Aaron's B2 — stable
  cluster_id derived from (tenant_id, entity_id, 24h-window-bucket) via
  uuid5 so refetches don't re-shuffle the UI
* Cursor pagination via base64-encoded ``(proposed_at, event_id)`` tuples

The query is a single large SELECT with LEFT JOINs against people,
organizations, photos, tenants, proposal_conflict, and prov_activities.
RLS is enforced by the existing app role's policies on action_event
(set during request setup by the FastAPI tenant middleware).
"""
# ruff: noqa: E501, S608
# S608 OK: f-strings build WHERE clauses from a closed allowlist; every value
# is bound through params. See _build_where for the construction.

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.schemas.inbox import (
    CaseFileLink,
    ClusterKind,
    ListPendingProposalsInput,
    ListPendingProposalsOutput,
    Proposal,
    ProposalCluster,
    ProposalCompliance,
)
from contact_ops.services.case_file_links import link_case_files_batch

logger = logging.getLogger(__name__)

# Cluster bucket: 24h windows aligned to UTC midnight. Cluster_id stays
# stable across refetches within the same UTC day for a given entity,
# even as proposals are approved/rejected.
_CLUSTER_BUCKET_HOURS = 24

# Exposure tags allowlist (C2). Any tag on the aggregate that matches
# escalates compliance.exposure. Unknown tags don't escalate.
_EXPOSURE_TAG_ORDER: dict[str, int] = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}
_EXPOSURE_TAG_MAP: dict[str, str] = {
    "legal": "high",
    "medical": "high",
    "hipaa": "high",
    "financial": "medium",
    "credentials": "high",
    "investigation": "high",
}
_DEFAULT_EXPOSURE = "low"
_NO_EXPOSURE_DEFAULT = "none"


def _exposure_max(*levels: str) -> str:
    return max(levels, key=lambda lvl: _EXPOSURE_TAG_ORDER.get(lvl, 0))


# Delete-classifier event types (C4)
_DELETE_EVENT_TYPES = {
    "contact.delete",
    "tag.remove",
    "edge.remove",
    "person.delete",
    "organization.delete",
}


# Pseudo-namespace for cluster uuid5 derivation
_CLUSTER_NAMESPACE = uuid.UUID("019256a0-c0c0-7fff-8000-000000000001")


def _cluster_id_for(*, tenant_id: uuid.UUID, aggregate_id: uuid.UUID, bucket: datetime) -> uuid.UUID:
    """Deterministic cluster id stable across refetches within a 24h bucket."""
    bucket_key = bucket.replace(minute=0, second=0, microsecond=0).isoformat()
    name = f"{tenant_id}|{aggregate_id}|{bucket_key}"
    return uuid.uuid5(_CLUSTER_NAMESPACE, name)


def _bucket_for(created_at: datetime) -> datetime:
    """Truncate to the start of a 24-hour cluster window (UTC midnight)."""
    aware = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    return aware.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _encode_cursor(*, proposed_at: datetime, event_id: uuid.UUID) -> str:
    raw = json.dumps(
        {"t": proposed_at.isoformat(), "e": str(event_id)}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    padding = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode((cursor + padding).encode()).decode()
    doc = json.loads(raw)
    return datetime.fromisoformat(doc["t"]), uuid.UUID(doc["e"])


def _classify_action(event_type: str) -> dict[str, bool | int]:
    is_dedup = event_type.startswith("dedup.")
    is_edge = event_type.startswith("relationship.") or event_type.startswith("edge.")
    is_delete = event_type in _DELETE_EVENT_TYPES
    return {"is_dedup": is_dedup, "is_edge": is_edge, "is_delete": is_delete}


def _fields_changed(
    payload_before: dict[str, Any] | None,
    payload_after: dict[str, Any],
) -> int:
    """Count symmetric diff of top-level keys.

    A field counts as 'changed' if its key is present in only one side OR
    if the values differ. Nested-dict-aware diff isn't needed for the
    inbox display — Phase 4 reasoning can do finer-grained.
    """
    before = payload_before or {}
    after = payload_after or {}
    keys = set(before.keys()) | set(after.keys())
    changed = 0
    for k in keys:
        if before.get(k) != after.get(k):
            changed += 1
    return changed


def _bulk_count(payload_after: dict[str, Any]) -> int:
    targets = payload_after.get("target_ids")
    if isinstance(targets, list) and targets:
        return len(targets)
    return 1


def _cluster_kind_for(cluster_proposals: list[dict[str, Any]]) -> ClusterKind:
    """Pick the right cluster_kind per design doc §11.5 ordering rules.

    First match wins.
    """
    # Rule 2: dedup-cluster
    dedup_count = sum(
        1 for p in cluster_proposals if p.get("event_type", "").startswith("dedup.")
    )
    if dedup_count >= 3:
        return "dedup"

    # Rule 3: agent-batch — same agent on multiple entities in last hour.
    # Within a single ProposalCluster (entity-scoped), this maps to "every
    # proposal in the cluster came from one agent." That's a noisier
    # variant of "entity" but still useful to surface.
    if cluster_proposals:
        slugs = {p["agent_slug"] for p in cluster_proposals}
        if len(slugs) == 1 and len(cluster_proposals) >= 5:
            return "agent-batch"

    # Rule 4: bulk-tag-lifecycle
    if cluster_proposals and all(
        (p.get("event_type", "").startswith("tag.") or p.get("event_type", "").startswith("lifecycle."))
        for p in cluster_proposals
    ):
        return "bulk-tag-lifecycle"

    # Default: entity-cluster
    return "entity"


async def list_pending_proposals(
    *,
    db: AsyncSession,
    redis: Redis,
    caller_tenant_id: uuid.UUID,
    payload: ListPendingProposalsInput,
) -> ListPendingProposalsOutput:
    """Execute the inbox list query and assemble Proposals + Clusters.

    ``caller_tenant_id`` is the tenant whose RLS context is active. When
    ``payload.tenant_ids`` is None we pass ``caller_tenant_id`` only;
    "all tenants" mode is left to a future federation-aware version.
    """
    where, params, target_status = _build_where(payload, caller_tenant_id)

    cursor_clause = ""
    if payload.cursor:
        try:
            cursor_t, cursor_e = _decode_cursor(payload.cursor)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid cursor: {exc}") from exc
        cursor_clause = (
            " AND (ae.proposed_at, ae.event_id) < "
            "(:cursor_proposed_at, CAST(:cursor_event_id AS uuid))"
        )
        params["cursor_proposed_at"] = cursor_t
        params["cursor_event_id"] = str(cursor_e)

    base_query = f"""
        SELECT
            ae.event_id,
            ae.event_type,
            ae.tenant_id,
            ae.target_tenant_id,
            ae.aggregate_id,
            ae.aggregate_type,
            ae.confidence,
            ae.rationale,
            ae.proposed_at,
            ae.snoozed_until,
            ae.status,
            ae.decision_payload,
            ae.payload,
            ae.reversibility_class,
            ae.trust_tier_at_creation,
            ae.trace_id,
            ae.evidence_pack_id,
            ae.parent_proposal_id,
            ae.actor->>'sub' AS agent_slug,
            COALESCE(ae.agent_version, ae.actor->>'agent_version') AS agent_version,
            t.slug AS tenant_slug,
            t.hipaa_mode AS tenant_hipaa_mode,
            -- For proposed person.create / org.create the entity row doesn't
            -- exist yet (it's only inserted on Approve), so fall back to the
            -- name carried in the proposal payload. Prevents the entire queue
            -- from rendering as "(unknown)" when proposed-only writes hit
            -- before any apply happened.
            COALESCE(
                p.display_name,
                o.display_name,
                ae.payload->'after'->>'display_name',
                ae.payload->'after'->>'legal_name'
            ) AS entity_display_name,
            ph.id AS primary_photo_id,
            EXISTS (
                SELECT 1 FROM proposal_conflict pc
                WHERE pc.primary_proposal_id = ae.event_id
                   OR pc.conflicting_proposal_id = ae.event_id
            ) AS has_conflict
        FROM action_event ae
        LEFT JOIN tenants t ON t.id = ae.tenant_id
        LEFT JOIN persons p
               ON p.id = ae.aggregate_id AND ae.aggregate_type = 'person'
        LEFT JOIN organizations o
               ON o.id = ae.aggregate_id AND ae.aggregate_type = 'organization'
        LEFT JOIN LATERAL (
            SELECT id FROM photos
            WHERE person_id = ae.aggregate_id
            ORDER BY is_primary DESC, observed_at DESC
            LIMIT 1
        ) ph ON ae.aggregate_type = 'person'
        WHERE {where}
        {cursor_clause}
        ORDER BY ae.proposed_at DESC, ae.event_id DESC
        LIMIT :limit
    """
    params["limit"] = payload.limit + 1  # fetch one extra to compute next_cursor

    result = await db.execute(text(base_query), params)
    rows = list(result.mappings())

    # Pagination boundary
    has_more = len(rows) > payload.limit
    rows = rows[: payload.limit]

    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = _encode_cursor(
            proposed_at=last["proposed_at"], event_id=uuid.UUID(str(last["event_id"]))
        )

    if not rows:
        return ListPendingProposalsOutput(
            clusters=[], proposals=[], next_cursor=None, total_estimate=0
        )

    # Apply the "snoozed" filter post-status (status='proposed' AND
    # snoozed_until > now()). Doing it in SQL would have made the cursor
    # comparison brittle; trivial filter here keeps the boundary clean.
    if target_status == "snoozed":
        now = datetime.now(UTC)
        rows = [
            r for r in rows
            if r["snoozed_until"] is not None and r["snoozed_until"] > now
        ]
    elif target_status == "proposed":
        now = datetime.now(UTC)
        rows = [
            r for r in rows
            if r["snoozed_until"] is None or r["snoozed_until"] <= now
        ]

    # Pre-resolve case_file_links for all distinct aggregate_ids in this
    # response page (batched, cached).
    case_links_by_aggregate = await link_case_files_batch(
        redis=redis,
        tenant_id=caller_tenant_id,
        aggregate_ids=list({uuid.UUID(str(r["aggregate_id"])) for r in rows}),
    )

    # Per-row exposure tags lookup. One round-trip for the page.
    tag_rows_by_aggregate = await _fetch_tags_for(
        db=db,
        aggregate_ids=list({uuid.UUID(str(r["aggregate_id"])) for r in rows}),
    )

    proposals: list[Proposal] = []
    clusters_by_id: dict[uuid.UUID, list[dict[str, Any]]] = {}

    now = datetime.now(UTC)
    for row in rows:
        proposal_id = uuid.UUID(str(row["event_id"]))
        tenant_id = uuid.UUID(str(row["tenant_id"]))
        aggregate_id = uuid.UUID(str(row["aggregate_id"]))
        decision_payload = row["decision_payload"] or {}
        payload_after = decision_payload.get("payload_after") or row["payload"] or {}
        payload_before = decision_payload.get("payload_before")

        classify = _classify_action(row["event_type"])
        compliance = _derive_compliance(
            tenant_hipaa_mode=bool(row["tenant_hipaa_mode"]),
            tags=tag_rows_by_aggregate.get(aggregate_id, []),
        )

        case_file_links_raw = case_links_by_aggregate.get(aggregate_id, [])
        case_file_links = [
            CaseFileLink(
                project_id=uuid.UUID(item["project_id"]),
                project_name=item["project_name"],
                task_id=uuid.UUID(item["task_id"]) if item.get("task_id") else None,
                task_name=item.get("task_name"),
            )
            for item in case_file_links_raw
        ]
        touches_case_file = len(case_file_links) > 0

        target_tenant = row["target_tenant_id"]
        cross_tenant = target_tenant is not None and uuid.UUID(str(target_tenant)) != tenant_id

        cluster_id = _cluster_id_for(
            tenant_id=tenant_id,
            aggregate_id=aggregate_id,
            bucket=_bucket_for(row["proposed_at"]),
        )

        agent_slug = row["agent_slug"] or "unknown"
        agent_version = row["agent_version"] or "unknown"

        avatar_url: str | None = None
        if row["primary_photo_id"] is not None:
            avatar_url = f"/api/photos/{row['primary_photo_id']}"

        proposal = Proposal(
            proposal_id=proposal_id,
            action_event_id=proposal_id,
            agent_id=agent_slug,
            agent_version=agent_version,
            tenant_id=tenant_id,
            tenant_slug=row["tenant_slug"] or "",
            entity_id=aggregate_id,
            entity_kind=row["aggregate_type"],
            entity_display_name=row["entity_display_name"] or "(unknown)",
            entity_avatar_url=avatar_url,
            action_type=row["event_type"],
            payload_before=payload_before,
            payload_after=payload_after,
            confidence=float(row["confidence"] or 0.0),
            reversibility_class=row["reversibility_class"] or "reversible",
            compliance=compliance,
            trust_tier_at_creation=int(row["trust_tier_at_creation"] or 0),
            trace_id=row["trace_id"],
            evidence_pack_id=(
                uuid.UUID(str(row["evidence_pack_id"]))
                if row["evidence_pack_id"] else None
            ),
            parent_proposal_id=(
                uuid.UUID(str(row["parent_proposal_id"]))
                if row["parent_proposal_id"] else None
            ),
            rationale=row["rationale"] or "",
            created_at=row["proposed_at"],
            snoozed_until=row["snoozed_until"],
            cross_tenant=cross_tenant,
            touches_case_file=touches_case_file,
            case_file_links=case_file_links,
            fields_changed=_fields_changed(payload_before, payload_after),
            is_dedup=bool(classify["is_dedup"]),
            is_edge=bool(classify["is_edge"]),
            is_delete=bool(classify["is_delete"]),
            bulk_count=_bulk_count(payload_after),
            cluster_id=cluster_id,
        )
        proposals.append(proposal)

        clusters_by_id.setdefault(cluster_id, []).append(
            {
                "proposal_id": proposal_id,
                "agent_slug": agent_slug,
                "event_type": row["event_type"],
                "confidence": proposal.confidence,
                "created_at": proposal.created_at,
                "entity_id": aggregate_id,
                "entity_display_name": proposal.entity_display_name,
                "entity_avatar_url": avatar_url,
                "tenant_id": tenant_id,
            }
        )

    clusters = _build_clusters(clusters_by_id)

    # Personal-org separation per D6: even within "all tenants" mode the
    # cluster bucket is keyed by (tenant_id, aggregate_id) so cross-tenant
    # entities never collapse. The defensive enforcement happens at the
    # cluster-key level; nothing further required here.

    # Best-effort total estimate — exact COUNT would force a second query.
    total_estimate = len(proposals) + (1 if has_more else 0)

    return ListPendingProposalsOutput(
        clusters=clusters,
        proposals=proposals,
        next_cursor=next_cursor,
        total_estimate=total_estimate,
    )


def _derive_compliance(*, tenant_hipaa_mode: bool, tags: list[str]) -> ProposalCompliance:
    """C2 implementation: hipaa from tenant; exposure from tag allowlist."""
    exposure = _NO_EXPOSURE_DEFAULT
    for tag in tags:
        mapped = _EXPOSURE_TAG_MAP.get(tag.lower())
        if mapped:
            exposure = _exposure_max(exposure, mapped)
    # If the tenant is HIPAA-flagged at all, never expose anything as
    # "none" — minimum baseline is "low" for the trust-ladder math.
    if tenant_hipaa_mode and exposure == "none":
        exposure = _DEFAULT_EXPOSURE
    return ProposalCompliance(hipaa=tenant_hipaa_mode, exposure=exposure)


async def _fetch_tags_for(
    *,
    db: AsyncSession,
    aggregate_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[str]]:
    """Fetch tag slugs for an aggregate.

    Tags live as ``text[]`` arrays on the per-tenant membership tables
    (Phase 0/1 schema). We UNION ALL the two membership tables so a
    single response covers both persons and organizations.
    """
    if not aggregate_ids:
        return {}
    result = await db.execute(
        text(
            """
            SELECT person_id AS entity_id, unnest(tags) AS tag_slug
            FROM person_tenant_membership
            WHERE person_id = ANY(CAST(:ids AS uuid[]))
            UNION ALL
            SELECT organization_id AS entity_id, unnest(tags) AS tag_slug
            FROM organization_tenant_membership
            WHERE organization_id = ANY(CAST(:ids AS uuid[]))
            """
        ),
        {"ids": [str(a) for a in aggregate_ids]},
    )
    out: dict[uuid.UUID, list[str]] = {}
    for row in result.mappings():
        eid = uuid.UUID(str(row["entity_id"]))
        out.setdefault(eid, []).append(row["tag_slug"])
    return out


def _build_clusters(
    clusters_by_id: dict[uuid.UUID, list[dict[str, Any]]],
) -> list[ProposalCluster]:
    out: list[ProposalCluster] = []
    for cluster_id, members in clusters_by_id.items():
        if not members:
            continue
        kind = _cluster_kind_for(members)
        confidences = [m["confidence"] for m in members if m["confidence"] is not None]
        avg = sum(confidences) / len(confidences) if confidences else 0.0
        created_ats = [m["created_at"] for m in members]
        first = members[0]
        out.append(
            ProposalCluster(
                cluster_id=cluster_id,
                cluster_kind=kind,
                entity_id=first["entity_id"],
                entity_display_name=first["entity_display_name"],
                entity_avatar_url=first["entity_avatar_url"],
                tenant_id=first["tenant_id"],
                proposal_ids=[m["proposal_id"] for m in members[:12]],
                cumulative_confidence_avg=avg,
                agent_slugs=sorted({m["agent_slug"] for m in members}),
                earliest_created_at=min(created_ats),
                latest_created_at=max(created_ats),
            )
        )
    out.sort(key=lambda c: c.latest_created_at, reverse=True)
    return out


def _build_where(
    payload: ListPendingProposalsInput,
    caller_tenant_id: uuid.UUID,
) -> tuple[str, dict[str, Any], str]:
    where: list[str] = []
    params: dict[str, Any] = {}

    if payload.tenant_ids is None:
        where.append("ae.tenant_id = CAST(:tenant AS uuid)")
        params["tenant"] = str(caller_tenant_id)
    else:
        ids = [str(t) for t in payload.tenant_ids]
        where.append("ae.tenant_id = ANY(CAST(:tenants AS uuid[]))")
        params["tenants"] = ids

    target_status = payload.status
    if target_status == "resolved":
        where.append("ae.status IN ('applied','rejected','reverted','superseded')")
    else:
        # both "proposed" and "snoozed" need status='proposed'; we slice
        # by snoozed_until in Python after fetch.
        where.append("ae.status = 'proposed'")

    if payload.agent_slugs:
        where.append("ae.actor->>'sub' = ANY(:agent_slugs)")
        params["agent_slugs"] = payload.agent_slugs

    if payload.confidence_min is not None:
        where.append("ae.confidence >= :c_min")
        params["c_min"] = payload.confidence_min
    if payload.confidence_max is not None:
        where.append("ae.confidence <= :c_max")
        params["c_max"] = payload.confidence_max

    if payload.action_types:
        where.append("ae.event_type = ANY(:action_types)")
        params["action_types"] = payload.action_types

    if payload.conflicts_only:
        where.append(
            "EXISTS (SELECT 1 FROM proposal_conflict pc "
            "WHERE pc.primary_proposal_id = ae.event_id "
            "OR pc.conflicting_proposal_id = ae.event_id)"
        )

    if payload.hipaa_only:
        where.append(
            "EXISTS (SELECT 1 FROM tenants t2 "
            "WHERE t2.id = ae.tenant_id AND t2.hipaa_mode = true)"
        )

    if payload.cross_tenant_only:
        where.append(
            "ae.target_tenant_id IS NOT NULL AND ae.target_tenant_id != ae.tenant_id"
        )

    if payload.entity_id is not None:
        where.append("ae.aggregate_id = CAST(:entity_id AS uuid)")
        params["entity_id"] = str(payload.entity_id)

    if payload.decided_by_user_id is not None:
        where.append(
            "EXISTS (SELECT 1 FROM inbox_decisions idec "
            "WHERE idec.proposal_id = ae.event_id "
            "AND idec.reviewer_id = CAST(:reviewer AS uuid))"
        )
        params["reviewer"] = str(payload.decided_by_user_id)

    where_clause = " AND ".join(where)
    return where_clause, params, target_status


async def get_proposal_for_decision(
    *,
    db: AsyncSession,
    proposal_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Fetch a single proposal for mutation tools (approve/reject/snooze).

    Returns the raw row (with tenant_hipaa_mode joined) or None if the
    proposal doesn't exist OR belongs to a different tenant. Mutation
    tools call this to enforce per-call tenant ownership; the RLS layer
    is also active, this is defense-in-depth.
    """
    result = await db.execute(
        text(
            """
            SELECT
                ae.event_id, ae.event_type, ae.tenant_id, ae.target_tenant_id,
                ae.aggregate_id, ae.aggregate_type, ae.confidence, ae.status,
                ae.snoozed_until, ae.applied_at, ae.proposed_at,
                ae.decision_payload, ae.payload, ae.reversibility_class,
                ae.trust_tier_at_creation,
                ae.actor->>'sub' AS agent_slug,
                t.hipaa_mode AS tenant_hipaa_mode
            FROM action_event ae
            LEFT JOIN tenants t ON t.id = ae.tenant_id
            WHERE ae.event_id = CAST(:id AS uuid)
              AND ae.tenant_id = CAST(:tenant AS uuid)
            """
        ),
        {"id": str(proposal_id), "tenant": str(tenant_id)},
    )
    row = result.mappings().first()
    return dict(row) if row else None


__all__ = [
    "_bucket_for",
    "_cluster_id_for",
    "_classify_action",
    "_derive_compliance",
    "_decode_cursor",
    "_encode_cursor",
    "_fields_changed",
    "_bulk_count",
    "get_proposal_for_decision",
    "list_pending_proposals",
]
