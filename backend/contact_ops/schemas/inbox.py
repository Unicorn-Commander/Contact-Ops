"""Inbox Pydantic schemas — shared by service layer + MCP tools.

The frontend Phase 3.3 (UI) prompt names these shapes verbatim; the field
names and casing here are the contract the React app calls against. Don't
rename without checking the UI prompt.
"""
# ruff: noqa: E501

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- core shared types ----

Tier = Literal[0, 1, 2, 3, 4]
"""0=auto-apply, 1=one-click, 2=reviewed, 3=diff-reviewed, 4=typed-phrase.
See Phase 3 Design §11.6 + UI prompt 'Friction ladder'."""

EntityKind = Literal["person", "org"]

ReversibilityClass = Literal["reversible", "reversible_24h", "soft_delete", "irreversible"]

ComplianceExposure = Literal["none", "low", "medium", "high"]

ProposalStatusFilter = Literal["proposed", "snoozed", "resolved"]
"""Filter taxonomy the frontend uses. Maps in inbox_query to:
- 'proposed' -> SQL status='proposed' AND (snoozed_until IS NULL OR snoozed_until <= now())
- 'snoozed'  -> SQL status='proposed' AND snoozed_until > now()
- 'resolved' -> SQL status IN ('applied','rejected','reverted','superseded')"""

ClusterKind = Literal["entity", "dedup", "agent-batch", "bulk-tag-lifecycle"]

RejectMode = Literal["reject", "dismiss_duplicate", "mute", "undo"]


class ProposalCompliance(BaseModel):
    hipaa: bool
    exposure: ComplianceExposure


class CaseFileLink(BaseModel):
    project_id: uuid.UUID
    project_name: str
    task_id: uuid.UUID | None = None
    task_name: str | None = None


class Proposal(BaseModel):
    """One reviewable agent proposal. Renders as a single row in the inbox.

    Frontend mirrors this exactly (see UI prompt 'Type extensions' section).
    """

    model_config = ConfigDict(populate_by_name=True)

    proposal_id: uuid.UUID
    action_event_id: uuid.UUID
    agent_id: str  # agent slug
    agent_version: str
    tenant_id: uuid.UUID
    tenant_slug: str
    entity_id: uuid.UUID
    entity_kind: EntityKind
    entity_display_name: str
    entity_avatar_url: str | None = None
    action_type: str  # e.g. "dedup.propose_merge"
    payload_before: dict[str, Any] | None
    payload_after: dict[str, Any]
    confidence: float = Field(ge=0, le=1)
    reversibility_class: ReversibilityClass
    compliance: ProposalCompliance
    trust_tier_at_creation: Tier
    trace_id: str | None = None
    evidence_pack_id: uuid.UUID | None = None
    parent_proposal_id: uuid.UUID | None = None
    rationale: str
    created_at: datetime
    snoozed_until: datetime | None = None
    cross_tenant: bool = False
    touches_case_file: bool = False
    case_file_links: list[CaseFileLink] = Field(default_factory=list)
    fields_changed: int = 0
    is_dedup: bool = False
    is_edge: bool = False
    is_delete: bool = False
    bulk_count: int = 1
    cluster_id: uuid.UUID  # server-assigned per B2


class ProposalCluster(BaseModel):
    """Server-side cluster grouping per design doc §11.5 + Aaron's B2."""

    cluster_id: uuid.UUID
    cluster_kind: ClusterKind
    entity_id: uuid.UUID
    entity_display_name: str
    entity_avatar_url: str | None = None
    tenant_id: uuid.UUID
    proposal_ids: list[uuid.UUID]
    cumulative_confidence_avg: float
    agent_slugs: list[str]
    earliest_created_at: datetime
    latest_created_at: datetime


# ---- list_pending_proposals ----


class ListPendingProposalsInput(BaseModel):
    """Filter shape locked-in by Aaron's B1 answer.

    tenant_ids=None means 'all tenants the caller can see' (frontend "All
    tenants" mode). Personal-org separation is enforced backend-side per
    C2/D6 — see services/inbox_query.py.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_ids: list[uuid.UUID] | None = None
    status: ProposalStatusFilter = "proposed"
    agent_slugs: list[str] | None = None
    confidence_min: float | None = Field(default=None, ge=0, le=1)
    confidence_max: float | None = Field(default=None, ge=0, le=1)
    action_types: list[str] | None = None
    conflicts_only: bool = False
    hipaa_only: bool = False
    cross_tenant_only: bool = False
    case_file_only: bool = False
    entity_id: uuid.UUID | None = None
    decided_by_user_id: uuid.UUID | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    top_calibrated_window: Literal["this_week", "this_month"] | None = None


class ListPendingProposalsOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    clusters: list[ProposalCluster]
    proposals: list[Proposal]
    next_cursor: str | None
    total_estimate: int


# ---- approve_proposal ----


class ApproveProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID
    field_choices: dict[str, Literal["master", "proposed", "custom"]] | None = None
    custom_values: dict[str, Any] | None = None
    typed_phrase: str | None = None
    tier_assigned: Tier
    time_to_decide_sec: int | None = Field(default=None, ge=0)
    keyboard_path: bool = False
    typed_phrase_used: bool = False


class ApproveProposalOutput(BaseModel):
    applied: bool
    action_event_id: uuid.UUID
    inbox_decision_id: uuid.UUID


# ---- reject_proposal ----


class RejectProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID
    mode: RejectMode = "reject"
    reason: str | None = Field(default=None, max_length=2048)
    tier_assigned: Tier = 1
    time_to_decide_sec: int | None = Field(default=None, ge=0)
    keyboard_path: bool = False
    # for mode="mute":
    suppression_aggregate_id: uuid.UUID | None = None
    suppression_field_name: str | None = Field(default=None, max_length=200)
    suppression_expires_at: datetime | None = None


class RejectProposalOutput(BaseModel):
    rejected: bool
    inbox_decision_id: uuid.UUID
    suppression_rule_id: uuid.UUID | None = None


# ---- snooze_proposal ----


SnoozeReason = Literal[
    "wait_for_event", "tomorrow", "end_of_week", "next_monday", "custom"
]


class SnoozeProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID
    snooze_until: datetime
    snooze_reason: SnoozeReason = "custom"
    pegged_event_id: uuid.UUID | None = None  # Project-Ops task id for "until X event"
    tier_assigned: Tier = 1
    keyboard_path: bool = False


class SnoozeProposalOutput(BaseModel):
    snoozed: bool
    inbox_decision_id: uuid.UUID


# ---- bulk_approve / bulk_reject ----


BulkSkipReason = Literal[
    "hipaa_requires_t4",
    "already_resolved",
    "cross_tenant_mixed",
    "tier_4_in_selection",
    "stale_optimistic_lock",
    "suppressed_by_rule",
    "not_found",
    "wrong_tenant",
    "merge_failed",
]


class BulkApproveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    typed_phrase: str | None = None
    tier_assigned: Tier = 1
    time_to_decide_sec: int | None = Field(default=None, ge=0)
    keyboard_path: bool = False


class BulkApproveOutput(BaseModel):
    applied: int
    skipped: list[uuid.UUID]
    reasons: dict[str, BulkSkipReason]
    inbox_decision_ids: list[uuid.UUID]


class BulkRejectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=2048)
    tier_assigned: Tier = 1
    time_to_decide_sec: int | None = Field(default=None, ge=0)
    keyboard_path: bool = False


class BulkRejectOutput(BaseModel):
    rejected: int
    skipped: list[uuid.UUID]
    reasons: dict[str, BulkSkipReason]
    inbox_decision_ids: list[uuid.UUID]


# ---- get_proposal_evidence ----


class SourceEvent(BaseModel):
    event_id: uuid.UUID
    kind: str
    title: str
    occurred_at: datetime
    deep_link: str


class ProposalEvidenceOutput(BaseModel):
    proposal_id: uuid.UUID
    source_events: list[SourceEvent]
    reasoning: str | None
    laminar_trace_url: str | None
    prov_activity_id: uuid.UUID | None
    cost_cents: int
    tokens_input: int
    tokens_output: int


class GetProposalEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID


# ---- revert_auto_applied ----


class RevertAutoAppliedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=2048)


class RevertAutoAppliedOutput(BaseModel):
    reverted: bool
    inbox_decision_id: uuid.UUID
    reverted_at: datetime
    age_seconds: int  # how long ago the auto-apply happened


# ---- resolve_conflict_keep_both ----


class ResolveConflictKeepBothInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_proposal_id: uuid.UUID
    conflicting_proposal_id: uuid.UUID
    source_tags: dict[uuid.UUID, str] = Field(
        description="proposal_id -> source tag string; both proposals must be present"
    )
    tier_assigned: Tier = 3
    time_to_decide_sec: int | None = Field(default=None, ge=0)


class ResolveConflictKeepBothOutput(BaseModel):
    primary_applied: bool
    conflicting_applied: bool
    conflict_id: uuid.UUID
    inbox_decision_id: uuid.UUID


# ---- proposal conflict surfacing (used by list query + frontend) ----


class ProposalConflictSummary(BaseModel):
    conflict_id: uuid.UUID
    primary_proposal_id: uuid.UUID
    conflicting_proposal_id: uuid.UUID
    conflict_type: Literal[
        "inverse_of_recent",
        "contradicting_field_value",
        "delete_vs_modify",
        "cascade_loop",
        "per_entity_cooldown_exceeded",
    ]
    entity_id: uuid.UUID
    field_name: str | None


__all__ = [
    "ApproveProposalInput",
    "ApproveProposalOutput",
    "BulkApproveInput",
    "BulkApproveOutput",
    "BulkRejectInput",
    "BulkRejectOutput",
    "BulkSkipReason",
    "CaseFileLink",
    "ClusterKind",
    "ComplianceExposure",
    "EntityKind",
    "GetProposalEvidenceInput",
    "ListPendingProposalsInput",
    "ListPendingProposalsOutput",
    "Proposal",
    "ProposalCluster",
    "ProposalCompliance",
    "ProposalConflictSummary",
    "ProposalEvidenceOutput",
    "ProposalStatusFilter",
    "RejectMode",
    "RejectProposalInput",
    "RejectProposalOutput",
    "ResolveConflictKeepBothInput",
    "ResolveConflictKeepBothOutput",
    "ReversibilityClass",
    "RevertAutoAppliedInput",
    "RevertAutoAppliedOutput",
    "SnoozeProposalInput",
    "SnoozeProposalOutput",
    "SnoozeReason",
    "SourceEvent",
    "Tier",
]
