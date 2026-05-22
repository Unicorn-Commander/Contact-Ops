"""Dedup admin MCP tools."""
# ruff: noqa: I001

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import (
    HIPAA_BLOCKED,
    PERSON_MERGED,
    PERSON_NOT_FOUND,
    ToolError,
    VALIDATION_FAILED,
)
from contact_ops.mcp.registry import MCPContext, ToolDef, register_tool
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.tools.common import ToolOutput, load_person, primary_email_for


# ---- Input models ----


class ProposeMergeInput(BaseModel):
    person_a_id: uuid.UUID
    person_b_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)


class ApplyMergeInput(BaseModel):
    action_event_id: uuid.UUID
    survivor_id: uuid.UUID | None = None


class UnmergeInput(BaseModel):
    person_alias_id: uuid.UUID


class DedupStatusInput(BaseModel):
    tenant_id: uuid.UUID | None = None


# ---- Output models ----


class ProposeMergeOutput(ToolOutput):
    action_event_id: str
    match_probability: float
    recommendation: str


class ApplyMergeOutput(ToolOutput):
    merge_history_id: str
    survivor_id: str
    alias_id: str
    can_unmerge: bool
    can_unmerge_until: str | None
    facts_promoted: int


class UnmergeOutput(ToolOutput):
    alias_id: str
    survivor_id: str
    facts_restored: int
    edges_restored: int


class DedupStatusOutput(ToolOutput):
    tenant_id: str
    agent_version: str
    active_splink_model_version: str | None
    last_run_at: str | None
    last_run_duration_sec: int | None
    candidates_evaluated_last_24h: int
    proposals_emitted_last_24h: int
    auto_merges_last_24h: int
    approved_last_24h: int
    rejected_last_24h: int
    active_proposals: int
    current_trust_tier: str
    stage: str
    stage_progress: str
    per_source_fp_rate_30d: dict[str, float]


# ---- Tool implementations ----


async def _propose_merge(ctx: MCPContext, args: BaseModel) -> ProposeMergeOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write", "dedup:propose"))
    data = ProposeMergeInput.model_validate(args)

    person_a = await load_person(ctx, data.person_a_id)
    person_b = await load_person(ctx, data.person_b_id)

    from contact_ops.agents.dedup.hipaa_fence import PersonRef, crosses_hipaa_fence

    if await crosses_hipaa_fence(
        PersonRef(data.person_a_id, ctx.tenant_id),
        PersonRef(data.person_b_id, ctx.tenant_id),
    ):
        raise ToolError(HIPAA_BLOCKED, "Cannot propose merge across HIPAA boundary", retryable=False)

    from contact_ops.agents.dedup.comparisons import (
        compare_email,
        compare_first_name,
        compare_last_name,
        compare_phone,
    )

    bits = 0.0
    bits += compare_first_name(person_a.given_name, person_b.given_name).raw_bits
    bits += compare_last_name(person_a.family_name, person_b.family_name).raw_bits

    email_a = await primary_email_for(ctx, data.person_a_id)
    email_b = await primary_email_for(ctx, data.person_b_id)
    bits += compare_email(email_a, email_b).raw_bits

    match_probability = (2**bits) / (1 + 2**bits) if bits != 0 else 0.5
    match_probability = min(max(match_probability, 0.0), 1.0)

    from contact_ops.agents.dedup.evidence_pack import (
        SideBySideField,
        WhatChangesIfMerged,
        build_evidence_pack,
    )

    side_by_side = [
        SideBySideField(
            field="given_name",
            a_value=person_a.given_name,
            b_value=person_b.given_name,
            comparison_level="provided",
        ),
        SideBySideField(
            field="family_name",
            a_value=person_a.family_name,
            b_value=person_b.family_name,
            comparison_level="provided",
        ),
        SideBySideField(
            field="email",
            a_value=email_a,
            b_value=email_b,
            comparison_level="provided",
        ),
    ]

    what_changes = WhatChangesIfMerged(
        survivor_id=data.person_a_id,
        alias_id=data.person_b_id,
    )

    evidence_pack = build_evidence_pack(
        person_a={
            "id": str(data.person_a_id),
            "display_name": person_a.display_name,
            "given_name": person_a.given_name,
            "family_name": person_a.family_name,
        },
        person_b={
            "id": str(data.person_b_id),
            "display_name": person_b.display_name,
            "given_name": person_b.given_name,
            "family_name": person_b.family_name,
        },
        match_probability=match_probability,
        match_weight_bits=bits,
        per_field_comparisons=side_by_side,
        source_provenance=[],
        what_changes=what_changes,
    )

    if match_probability >= 0.95:
        recommendation = "auto_merge"
    elif match_probability >= 0.65:
        recommendation = "single_review"
    elif match_probability >= 0.40:
        recommendation = "batch_review"
    else:
        recommendation = "discard"

    event_id = await emit_action_event(
        ctx,
        event_type="dedup.propose_merge",
        aggregate_type="person",
        aggregate_id=data.person_a_id,
        affected_ids=[data.person_b_id],
        payload_before=None,
        payload_after={"evidence_pack": evidence_pack, "person_b_id": str(data.person_b_id)},
        rationale=data.reason,
        status="proposed",
    )

    return ProposeMergeOutput(
        action_event_id=str(event_id),
        match_probability=match_probability,
        recommendation=recommendation,
    )


async def _apply_merge(ctx: MCPContext, args: BaseModel) -> ApplyMergeOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write", "dedup:apply"))
    data = ApplyMergeInput.model_validate(args)

    ev_result = await ctx.audit_db.execute(
        text("""
            SELECT event_id, event_type, status, tenant_id, evidence
            FROM action_event
            WHERE event_id = CAST(:event_id AS uuid)
        """),
        {"event_id": str(data.action_event_id)},
    )
    ev_row = ev_result.mappings().first()
    if ev_row is None:
        raise ToolError(VALIDATION_FAILED, "action_event not found", retryable=False)
    if ev_row["event_type"] != "dedup.propose_merge":
        raise ToolError(
            VALIDATION_FAILED,
            f"expected dedup.propose_merge, got {ev_row['event_type']}",
            retryable=False,
        )
    if ev_row["status"] != "proposed":
        raise ToolError(
            VALIDATION_FAILED,
            f"action_event status is '{ev_row['status']}', expected 'proposed'",
            retryable=False,
        )

    evidence: dict[str, Any] = ev_row["evidence"]
    person_a_id = uuid.UUID(str(evidence["person_a_id"]))
    person_b_id = uuid.UUID(str(evidence["person_b_id"]))

    await load_person(ctx, person_a_id)
    await load_person(ctx, person_b_id)

    from contact_ops.agents.dedup.merge_executor import apply_merge

    try:
        result = await apply_merge(
            action_event_id=data.action_event_id,
            db=ctx.db,
            audit_db=ctx.audit_db,
            survivor_override=data.survivor_id,
        )
    except ValueError as exc:
        raise ToolError(VALIDATION_FAILED, str(exc), retryable=False) from exc

    await ctx.audit_db.execute(
        text("""
            UPDATE action_event
            SET status = 'applied', applied_at = now()
            WHERE event_id = CAST(:event_id AS uuid)
        """),
        {"event_id": str(data.action_event_id)},
    )

    await emit_action_event(
        ctx,
        event_type="dedup.merge_applied",
        aggregate_type="person",
        aggregate_id=result.survivor_id,
        affected_ids=[result.alias_id, result.survivor_id],
        payload_before=None,
        payload_after={
            "merge_history_id": str(result.merge_history_id),
            "survivor_id": str(result.survivor_id),
            "alias_id": str(result.alias_id),
            "facts_promoted": result.facts_promoted,
        },
        evidence={"supersedes_event_id": str(data.action_event_id)},
    )

    return ApplyMergeOutput(
        merge_history_id=str(result.merge_history_id),
        survivor_id=str(result.survivor_id),
        alias_id=str(result.alias_id),
        can_unmerge=result.can_unmerge,
        can_unmerge_until=result.can_unmerge_until.isoformat() if result.can_unmerge_until else None,
        facts_promoted=result.facts_promoted,
    )


async def _unmerge(ctx: MCPContext, args: BaseModel) -> UnmergeOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write", "dedup:apply"))
    data = UnmergeInput.model_validate(args)

    alias_result = await ctx.db.execute(
        text("""
            SELECT pa.alias_id, pa.current_canonical_id AS survivor_id,
                   p.canonical_owner_tenant_id AS tenant_id
            FROM person_alias pa
            JOIN persons p ON p.id = pa.alias_id
            WHERE pa.alias_id = CAST(:alias_id AS uuid)
        """),
        {"alias_id": str(data.person_alias_id)},
    )
    alias_row = alias_result.mappings().first()
    if alias_row is None:
        raise ToolError(VALIDATION_FAILED, "person_alias not found", retryable=False)

    alias_tenant_id = uuid.UUID(str(alias_row["tenant_id"]))
    if alias_tenant_id != ctx.tenant_id:
        raise ToolError(VALIDATION_FAILED, "tenant mismatch", retryable=False)

    await load_person(ctx, uuid.UUID(str(alias_row["survivor_id"])))

    from contact_ops.agents.dedup.unmerge_executor import unmerge

    try:
        unmerge_result = await unmerge(
            person_alias_id=data.person_alias_id,
            db=ctx.db,
            audit_db=ctx.audit_db,
        )
    except ValueError as exc:
        raise ToolError(VALIDATION_FAILED, str(exc), retryable=False) from exc

    return UnmergeOutput(
        alias_id=str(unmerge_result.alias_id),
        survivor_id=str(unmerge_result.survivor_id),
        facts_restored=unmerge_result.facts_restored,
        edges_restored=unmerge_result.edges_restored,
    )


async def _dedup_status(ctx: MCPContext, args: BaseModel) -> DedupStatusOutput:
    require_scopes(ctx, ("dedup:read",))
    data = DedupStatusInput.model_validate(args)

    resolved_tenant_id = data.tenant_id or ctx.tenant_id

    if data.tenant_id and data.tenant_id != ctx.tenant_id:
        require_role(ctx, "ADMIN")

    last_run = await ctx.db.execute(
        text("""
            SELECT proposed_at
            FROM action_event
            WHERE tenant_id = CAST(:tid AS uuid)
              AND event_type = 'dedup.propose_merge'
            ORDER BY proposed_at DESC
            LIMIT 1
        """),
        {"tid": str(resolved_tenant_id)},
    )
    last_run_row = last_run.mappings().first()

    counts = await ctx.db.execute(
        text("""
            SELECT status, COUNT(*) AS cnt
            FROM action_event
            WHERE tenant_id = CAST(:tid AS uuid)
              AND event_type = 'dedup.propose_merge'
              AND proposed_at >= now() - interval '24 hours'
            GROUP BY status
        """),
        {"tid": str(resolved_tenant_id)},
    )
    status_counts: dict[str, int] = {}
    for row in counts.mappings():
        status_counts[row["status"]] = row["cnt"]

    cal = await ctx.db.execute(
        text("""
            SELECT source_kind, confidence_multiplier
            FROM source_calibration
            WHERE tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": str(resolved_tenant_id)},
    )
    per_source_fp: dict[str, float] = {}
    for row in cal.mappings():
        per_source_fp[row["source_kind"]] = 1.0 - float(row["confidence_multiplier"])

    return DedupStatusOutput(
        tenant_id=str(resolved_tenant_id),
        agent_version="0.1.0",
        active_splink_model_version=None,
        last_run_at=last_run_row["proposed_at"].isoformat() if last_run_row else None,
        last_run_duration_sec=None,
        candidates_evaluated_last_24h=status_counts.get("proposed", 0),
        proposals_emitted_last_24h=status_counts.get("proposed", 0),
        auto_merges_last_24h=status_counts.get("applied", 0),
        approved_last_24h=0,
        rejected_last_24h=0,
        active_proposals=status_counts.get("proposed", 0),
        current_trust_tier="T0",
        stage="A",
        stage_progress="0/30 days",
        per_source_fp_rate_30d=per_source_fp,
    )


# ---- Register tools ----


def register_dedup_admin_tools() -> None:
    base = {"destructiveHint": False, "openWorldHint": False}

    register_tool(ToolDef(
        name="propose_merge",
        description="Propose a merge between two persons. Runs comparison scoring and builds an evidence pack.",
        input_model=ProposeMergeInput,
        output_model=ProposeMergeOutput,
        handler=_propose_merge,
        required_role="STAFF",
        required_scopes=("person:write", "dedup:propose"),
        annotations={**base, "readOnlyHint": False, "idempotentHint": False},
        idempotency="not-idempotent",
    ))

    register_tool(ToolDef(
        name="apply_merge",
        description="Apply an approved merge proposal. Idempotent on action_event_id.",
        input_model=ApplyMergeInput,
        output_model=ApplyMergeOutput,
        handler=_apply_merge,
        required_role="STAFF",
        required_scopes=("person:write", "dedup:apply"),
        annotations={**base, "readOnlyHint": False, "idempotentHint": True},
        idempotency="natural-key",
    ))

    register_tool(ToolDef(
        name="unmerge",
        description="Reverse a previously applied merge. Idempotent on person_alias_id.",
        input_model=UnmergeInput,
        output_model=UnmergeOutput,
        handler=_unmerge,
        required_role="STAFF",
        required_scopes=("person:write", "dedup:apply"),
        annotations={**base, "readOnlyHint": False, "idempotentHint": True},
        idempotency="natural-key",
    ))

    register_tool(ToolDef(
        name="dedup_status",
        description="Get dedup agent status and statistics for the caller's tenant (or a specified tenant if ADMIN).",
        input_model=DedupStatusInput,
        output_model=DedupStatusOutput,
        handler=_dedup_status,
        required_role="CLIENT",
        required_scopes=("dedup:read",),
        annotations={**base, "readOnlyHint": True, "idempotentHint": True},
        idempotency="none",
    ))


register_dedup_admin_tools()
