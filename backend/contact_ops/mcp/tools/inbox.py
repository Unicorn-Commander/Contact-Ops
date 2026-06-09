"""Inbox MCP tools — the surface the Phase 3.3b frontend calls against.

Nine tools, all STAFF-role gated, scope ``contactops:inbox.review``:

* ``list_pending_proposals``       — read; the inbox list
* ``approve_proposal``              — write; tier-aware approval
* ``reject_proposal``               — write; mode dispatch (reject/dismiss_duplicate/mute/undo)
* ``snooze_proposal``               — write; sets snoozed_until
* ``bulk_approve``                  — write; best-effort + skip reasons
* ``bulk_reject``                   — write; same pattern
* ``get_proposal_evidence``         — read; EvidencePanel payload
* ``revert_auto_applied``           — write; ≤5min T0 revert
* ``resolve_conflict_keep_both``    — write; promotes both proposals as facts

All tools are tenant-scoped via the standard FastAPI middleware (sets
``app.current_tenant`` for RLS). Mutations re-validate tenant ownership
defensively via ``get_proposal_for_decision``.

Telemetry: every mutation passes the (tier, time_to_decide_sec,
keyboard_path, typed_phrase_used) telemetry fields through to
``inbox_decisions`` — the Calibration Daemon (Phase 3.4) consumes this.
"""
# ruff: noqa: E501

from __future__ import annotations

import uuid

from redis.asyncio import Redis

from contact_ops.core.config import get_settings
from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import register
from contact_ops.schemas.inbox import (
    ApproveProposalInput,
    ApproveProposalOutput,
    BulkApproveInput,
    BulkApproveOutput,
    BulkRejectInput,
    BulkRejectOutput,
    GetProposalEvidenceInput,
    ListPendingProposalsInput,
    ListPendingProposalsOutput,
    ProposalEvidenceOutput,
    RejectProposalInput,
    RejectProposalOutput,
    ResolveConflictKeepBothInput,
    ResolveConflictKeepBothOutput,
    RevertAutoAppliedInput,
    RevertAutoAppliedOutput,
    SnoozeProposalInput,
    SnoozeProposalOutput,
)
from contact_ops.services.inbox_evidence import get_proposal_evidence
from contact_ops.services.inbox_mutations import (
    InboxMutationError,
    approve_proposal,
    bulk_approve,
    bulk_reject,
    reject_proposal,
    resolve_conflict_keep_both,
    revert_auto_applied,
    snooze_proposal,
)
from contact_ops.services.inbox_query import list_pending_proposals

INBOX_SCOPE = "contactops:inbox.review"
# Reads use the same `proposal:read` scope the UI + tokens actually carry (the
# review queue is just a proposal list). `contactops:inbox.review` was declared
# on every inbox tool but is unseeded in Keycloak, so the chat executor — the
# one caller that enforces declared scopes — was wrongly 403ing reads for users
# who can clearly see the queue. Decisions (approve/reject/...) keep INBOX_SCOPE.
INBOX_READ_SCOPE = "proposal:read"
INBOX_ROLE = "STAFF"


def _open_redis() -> Redis:
    settings = get_settings()
    redis: Redis = Redis.from_url(settings.REDIS_URL)
    return redis


def _reviewer_id(ctx: MCPContext) -> uuid.UUID:
    try:
        return uuid.UUID(ctx.user_id)
    except ValueError as exc:
        raise ToolError(VALIDATION_FAILED, f"invalid user_id: {ctx.user_id}", retryable=False) from exc


def _wrap_mutation(exc: InboxMutationError) -> ToolError:
    return ToolError(exc.code, exc.message, retryable=False)


# ---- list_pending_proposals ----


async def _handle_list_pending_proposals(
    ctx: MCPContext, payload: ListPendingProposalsInput
) -> ListPendingProposalsOutput:
    redis = _open_redis()
    try:
        return await list_pending_proposals(
            db=ctx.db,
            redis=redis,
            caller_tenant_id=ctx.tenant_id,
            payload=payload,
        )
    finally:
        await redis.aclose()


# ---- approve_proposal ----


async def _handle_approve_proposal(
    ctx: MCPContext, payload: ApproveProposalInput
) -> ApproveProposalOutput:
    try:
        result = await approve_proposal(
            db=ctx.audit_db,
            app_db=ctx.db,
            tenant_id=ctx.tenant_id,
            reviewer_id=_reviewer_id(ctx),
            proposal_id=payload.proposal_id,
            tier_assigned=payload.tier_assigned,
            typed_phrase=payload.typed_phrase,
            field_choices=payload.field_choices,
            custom_values=payload.custom_values,
            time_to_decide_sec=payload.time_to_decide_sec,
            keyboard_path=payload.keyboard_path,
            typed_phrase_used=payload.typed_phrase_used,
        )
    except InboxMutationError as exc:
        raise _wrap_mutation(exc) from exc
    await ctx.audit_db.commit()
    return ApproveProposalOutput(**result)


# ---- reject_proposal ----


async def _handle_reject_proposal(
    ctx: MCPContext, payload: RejectProposalInput
) -> RejectProposalOutput:
    try:
        result = await reject_proposal(
            db=ctx.audit_db,
            tenant_id=ctx.tenant_id,
            reviewer_id=_reviewer_id(ctx),
            proposal_id=payload.proposal_id,
            mode=payload.mode,
            reason=payload.reason,
            tier_assigned=payload.tier_assigned,
            time_to_decide_sec=payload.time_to_decide_sec,
            keyboard_path=payload.keyboard_path,
            suppression_aggregate_id=payload.suppression_aggregate_id,
            suppression_field_name=payload.suppression_field_name,
            suppression_expires_at=payload.suppression_expires_at,
        )
    except InboxMutationError as exc:
        raise _wrap_mutation(exc) from exc
    await ctx.audit_db.commit()
    return RejectProposalOutput(**result)


# ---- snooze_proposal ----


async def _handle_snooze_proposal(
    ctx: MCPContext, payload: SnoozeProposalInput
) -> SnoozeProposalOutput:
    try:
        result = await snooze_proposal(
            db=ctx.audit_db,
            tenant_id=ctx.tenant_id,
            reviewer_id=_reviewer_id(ctx),
            proposal_id=payload.proposal_id,
            snooze_until=payload.snooze_until,
            snooze_reason=payload.snooze_reason,
            pegged_event_id=payload.pegged_event_id,
            tier_assigned=payload.tier_assigned,
            keyboard_path=payload.keyboard_path,
        )
    except InboxMutationError as exc:
        raise _wrap_mutation(exc) from exc
    await ctx.audit_db.commit()
    return SnoozeProposalOutput(**result)


# ---- bulk_approve / bulk_reject ----


async def _handle_bulk_approve(
    ctx: MCPContext, payload: BulkApproveInput
) -> BulkApproveOutput:
    try:
        result = await bulk_approve(
            db=ctx.audit_db,
            tenant_id=ctx.tenant_id,
            reviewer_id=_reviewer_id(ctx),
            proposal_ids=payload.proposal_ids,
            typed_phrase=payload.typed_phrase,
            tier_assigned=payload.tier_assigned,
            time_to_decide_sec=payload.time_to_decide_sec,
            keyboard_path=payload.keyboard_path,
        )
    except InboxMutationError as exc:
        raise _wrap_mutation(exc) from exc
    await ctx.audit_db.commit()
    return BulkApproveOutput(**result)


async def _handle_bulk_reject(
    ctx: MCPContext, payload: BulkRejectInput
) -> BulkRejectOutput:
    try:
        result = await bulk_reject(
            db=ctx.audit_db,
            tenant_id=ctx.tenant_id,
            reviewer_id=_reviewer_id(ctx),
            proposal_ids=payload.proposal_ids,
            reason=payload.reason,
            tier_assigned=payload.tier_assigned,
            time_to_decide_sec=payload.time_to_decide_sec,
            keyboard_path=payload.keyboard_path,
        )
    except InboxMutationError as exc:
        raise _wrap_mutation(exc) from exc
    await ctx.audit_db.commit()
    return BulkRejectOutput(**result)


# ---- get_proposal_evidence ----


async def _handle_get_proposal_evidence(
    ctx: MCPContext, payload: GetProposalEvidenceInput
) -> ProposalEvidenceOutput:
    result = await get_proposal_evidence(
        db=ctx.db,
        tenant_id=ctx.tenant_id,
        proposal_id=payload.proposal_id,
    )
    if result is None:
        raise ToolError(
            "PROPOSAL_NOT_FOUND",
            "proposal not found in this tenant",
            retryable=False,
        )
    return result


# ---- revert_auto_applied ----


async def _handle_revert_auto_applied(
    ctx: MCPContext, payload: RevertAutoAppliedInput
) -> RevertAutoAppliedOutput:
    try:
        result = await revert_auto_applied(
            db=ctx.audit_db,
            tenant_id=ctx.tenant_id,
            reviewer_id=_reviewer_id(ctx),
            proposal_id=payload.proposal_id,
            reason=payload.reason,
        )
    except InboxMutationError as exc:
        raise _wrap_mutation(exc) from exc
    await ctx.audit_db.commit()
    return RevertAutoAppliedOutput(**result)


# ---- resolve_conflict_keep_both ----


async def _handle_resolve_conflict_keep_both(
    ctx: MCPContext, payload: ResolveConflictKeepBothInput
) -> ResolveConflictKeepBothOutput:
    try:
        result = await resolve_conflict_keep_both(
            db=ctx.audit_db,
            tenant_id=ctx.tenant_id,
            reviewer_id=_reviewer_id(ctx),
            primary_proposal_id=payload.primary_proposal_id,
            conflicting_proposal_id=payload.conflicting_proposal_id,
            source_tags=payload.source_tags,
            tier_assigned=payload.tier_assigned,
            time_to_decide_sec=payload.time_to_decide_sec,
        )
    except InboxMutationError as exc:
        raise _wrap_mutation(exc) from exc
    await ctx.audit_db.commit()
    return ResolveConflictKeepBothOutput(**result)


# ---- registration ----


def register_inbox_tools() -> None:
    """Register all 9 Phase 3.3a inbox MCP tools."""

    register(
        name="list_pending_proposals",
        description=(
            "List pending proposals + clusters for the inbox UI. Supports "
            "filtering by tenant, agent, confidence range, action type, "
            "HIPAA, cross-tenant, case-file, conflicts, and per-entity. "
            "Returns server-side cluster groupings with stable cluster_ids."
        ),
        input_model=ListPendingProposalsInput,
        output_model=ListPendingProposalsOutput,
        handler=_handle_list_pending_proposals,
        required_role=INBOX_ROLE,
        required_scopes=(INBOX_READ_SCOPE,),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="repeatable",
    )
    register(
        name="approve_proposal",
        description=(
            "Approve a single proposal. Tier-aware: server re-validates the "
            "client-supplied tier and any typed-phrase (Tier 4). On success "
            "flips action_event.status to 'applied' and writes an "
            "inbox_decisions row with the telemetry fields."
        ),
        input_model=ApproveProposalInput,
        output_model=ApproveProposalOutput,
        handler=_handle_approve_proposal,
        required_role=INBOX_ROLE,
        required_scopes=(INBOX_SCOPE,),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )
    register(
        name="reject_proposal",
        description=(
            "Reject a proposal. mode='reject' (trains agent), "
            "'dismiss_duplicate' (silences but doesn't train), 'mute' "
            "(creates suppression rule), 'undo' (reverses an applied "
            "decision within 30s server window)."
        ),
        input_model=RejectProposalInput,
        output_model=RejectProposalOutput,
        handler=_handle_reject_proposal,
        required_role=INBOX_ROLE,
        required_scopes=(INBOX_SCOPE,),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )
    register(
        name="snooze_proposal",
        description=(
            "Snooze a proposal until a future datetime. snooze_until must be "
            "in the future. Snooze auto-clears on conflict via DB trigger."
        ),
        input_model=SnoozeProposalInput,
        output_model=SnoozeProposalOutput,
        handler=_handle_snooze_proposal,
        required_role=INBOX_ROLE,
        required_scopes=(INBOX_SCOPE,),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )
    register(
        name="bulk_approve",
        description=(
            "Best-effort bulk approve. Skips Tier-4 / HIPAA / already-resolved "
            "proposals with explicit reasons. >10 items requires typed_phrase "
            "'approve N items'."
        ),
        input_model=BulkApproveInput,
        output_model=BulkApproveOutput,
        handler=_handle_bulk_approve,
        required_role=INBOX_ROLE,
        required_scopes=(INBOX_SCOPE,),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )
    register(
        name="bulk_reject",
        description="Best-effort bulk reject. Skips already-resolved proposals.",
        input_model=BulkRejectInput,
        output_model=BulkRejectOutput,
        handler=_handle_bulk_reject,
        required_role=INBOX_ROLE,
        required_scopes=(INBOX_SCOPE,),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )
    register(
        name="get_proposal_evidence",
        description=(
            "Return the EvidencePanel payload: source events on the same "
            "aggregate (last 30 days), the agent's reasoning trace from "
            "prov_activities, a Laminar deep-link, and token/cost telemetry."
        ),
        input_model=GetProposalEvidenceInput,
        output_model=ProposalEvidenceOutput,
        handler=_handle_get_proposal_evidence,
        required_role=INBOX_ROLE,
        required_scopes=(INBOX_READ_SCOPE,),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="repeatable",
    )
    register(
        name="revert_auto_applied",
        description=(
            "Revert a T0 auto-applied proposal within the 5-minute server "
            "window. Distinct from reject_proposal(mode='undo') which "
            "targets a same-session approval; this targets agent-initiated "
            "auto-applies."
        ),
        input_model=RevertAutoAppliedInput,
        output_model=RevertAutoAppliedOutput,
        handler=_handle_revert_auto_applied,
        required_role=INBOX_ROLE,
        required_scopes=(INBOX_SCOPE,),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )
    register(
        name="resolve_conflict_keep_both",
        description=(
            "Resolve a proposal_conflict by promoting BOTH proposals as "
            "facts tagged with their source. This is the first-class "
            "'Keep both' button on the ConflictBanner."
        ),
        input_model=ResolveConflictKeepBothInput,
        output_model=ResolveConflictKeepBothOutput,
        handler=_handle_resolve_conflict_keep_both,
        required_role=INBOX_ROLE,
        required_scopes=(INBOX_SCOPE,),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )


register_inbox_tools()
