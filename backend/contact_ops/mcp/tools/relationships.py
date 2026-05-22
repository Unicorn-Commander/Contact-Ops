"""Person-to-person relationship MCP tools."""
# ruff: noqa: E501,I001

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import (
    EDGE_NOT_FOUND,
    RELATIONSHIP_ALREADY_EXISTS,
    ToolError,
    VALIDATION_FAILED,
)
from contact_ops.mcp.idempotency import check_or_register, store_result
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.tools.common import (
    PersonSummary,
    ToolOutput,
    load_person,
    person_summary,
    register,
    stable_hash,
)
from contact_ops.models import Person, PersonPersonRelation
from contact_ops.models.enums import RelationType

DesignRelation = Literal[
    "spouse",
    "domestic_partner",
    "parent",
    "child",
    "sibling",
    "grandparent",
    "grandchild",
    "aunt_uncle",
    "niece_nephew",
    "cousin",
    "father_in_law",
    "mother_in_law",
    "brother_in_law",
    "sister_in_law",
    "son_in_law",
    "daughter_in_law",
    "stepparent",
    "stepchild",
    "stepsibling",
    "adopted_parent",
    "adopted_child",
    "godparent",
    "godchild",
    "partner",
    "crush",
    "ex_spouse",
    "ex_partner",
    "friend",
    "close_friend",
    "best_friend",
    "acquaintance",
    "met",
    "neighbor",
    "roommate",
    "co_resident",
    "colleague",
    "manager",
    "direct_report",
    "skip_level_manager",
    "mentor",
    "mentee",
    "coach",
    "coachee",
    "advisor",
    "advisee",
    "peer",
    "business_partner",
    "co_founder",
    "investor_in",
    "client",
    "vendor",
    "referrer",
    "referred",
    "assistant_to",
    "assistant_of",
    "counsel_for",
    "client_of_counsel",
    "co_party",
    "opposing_party",
    "witness_for",
    "expert_witness_for",
    "judge_in_case",
    "plaintiff_in",
    "defendant_in",
    "self",
    "emergency_contact",
    "next_of_kin",
    "medical_proxy",
    "power_of_attorney",
    "executor",
    "beneficiary",
    "introduced_to",
    "introduced_by",
    "custom",
]


class LinkRelationshipInput(BaseModel):
    from_person_id: uuid.UUID
    to_person_id: uuid.UUID
    relation_type: DesignRelation
    custom_relation_label: str | None = Field(default=None, max_length=80)
    since: datetime | None = None
    until: datetime | None = None
    strength: float = Field(default=0.5, ge=0, le=1)
    context: str | None = Field(default=None, max_length=500)
    case_id: uuid.UUID | None = None
    source_label: str | None = Field(default=None, max_length=120)
    confidence: float = Field(default=1.0, ge=0, le=1)
    idempotency_key: uuid.UUID | None = None


class RelationshipOutput(ToolOutput):
    edge_id: uuid.UUID
    from_person_id: uuid.UUID
    to_person_id: uuid.UUID
    relation_type: str
    inverse_relation_type: str
    status: Literal["applied", "proposed"]
    event_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None


class UnlinkRelationshipInput(BaseModel):
    edge_id: uuid.UUID | None = None
    from_person_id: uuid.UUID | None = None
    to_person_id: uuid.UUID | None = None
    relation_type: str | None = None
    ended_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=500)
    hard_remove: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)


class UnlinkRelationshipOutput(ToolOutput):
    edge_id: uuid.UUID
    status: Literal["applied", "proposed"]
    ended_at: datetime
    event_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None


class ListRelationshipsInput(BaseModel):
    person_id: uuid.UUID
    direction: Literal["outgoing", "incoming", "both"] = "both"
    type_family_any: (
        list[
            Literal["family", "romantic", "social", "professional", "legal", "emergency", "custom"]
        ]
        | None
    ) = None
    relation_type_any: list[str] | None = Field(default=None, max_length=40)
    active_only: bool = True
    as_of: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None


class RelationItem(BaseModel):
    edge_id: uuid.UUID
    from_person_id: uuid.UUID
    to_person_id: uuid.UUID
    relation_type: str
    since: date | None = None
    until: date | None = None
    context: str | None = None
    case_id: uuid.UUID | None = None
    other_person_summary: PersonSummary


class ListRelationshipsOutput(ToolOutput):
    items: list[RelationItem]
    count: int
    next_cursor: str | None = None


class SuggestRelationshipsInput(BaseModel):
    person_id: uuid.UUID
    type_hints: list[Literal["family", "professional", "social", "legal"]] | None = None
    min_score: float = Field(default=0.5, ge=0, le=1)
    limit: int = Field(default=10, ge=1, le=50)


class SuggestRelationshipsOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int


class BulkLinkRelationshipsInput(BaseModel):
    items: list[LinkRelationshipInput] = Field(min_length=1, max_length=50)
    source_label: str = Field(max_length=120)
    idempotency_key: uuid.UUID | None = None


class BulkLinkResult(BaseModel):
    index: int
    edge_id: uuid.UUID | None = None
    action: str
    error_code: str | None = None


class BulkLinkRelationshipsOutput(ToolOutput):
    created: int
    noop: int
    errors: int
    results: list[BulkLinkResult]
    batch_event_id: uuid.UUID | None = None


_RELATION_MAP: dict[str, RelationType] = {
    "spouse": RelationType.spouse_of,
    "domestic_partner": RelationType.partner_of,
    "partner": RelationType.partner_of,
    "parent": RelationType.parent_of,
    "child": RelationType.child_of,
    "sibling": RelationType.sibling_of,
    "grandparent": RelationType.grandparent_of,
    "grandchild": RelationType.grandchild_of,
    "aunt_uncle": RelationType.aunt_uncle_of,
    "niece_nephew": RelationType.niece_nephew_of,
    "cousin": RelationType.cousin_of,
    "stepparent": RelationType.step_parent_of,
    "stepchild": RelationType.step_child_of,
    "stepsibling": RelationType.step_sibling_of,
    "adopted_parent": RelationType.adopted_parent_of,
    "adopted_child": RelationType.adopted_child_of,
    "ex_spouse": RelationType.ex_spouse_of,
    "friend": RelationType.friend_of,
    "close_friend": RelationType.close_friend_of,
    "best_friend": RelationType.close_friend_of,
    "acquaintance": RelationType.acquaintance_of,
    "met": RelationType.met_once,
    "neighbor": RelationType.knows,
    "roommate": RelationType.roommate_of,
    "co_resident": RelationType.household_member,
    "colleague": RelationType.colleague_of,
    "manager": RelationType.manager_of,
    "direct_report": RelationType.reports_to,
    "mentor": RelationType.mentor_of,
    "mentee": RelationType.mentee_of,
    "co_founder": RelationType.co_founder_of,
    "client": RelationType.client_of,
    "vendor": RelationType.vendor_of,
    "referrer": RelationType.referrer_of,
    "referred": RelationType.referred_by,
    "counsel_for": RelationType.counsel_for,
    "client_of_counsel": RelationType.client_of_counsel,
    "witness_for": RelationType.witness_for,
    "expert_witness_for": RelationType.expert_for,
    "executor": RelationType.executor_for,
    "beneficiary": RelationType.beneficiary_of,
    "emergency_contact": RelationType.emergency_contact_for,
    "next_of_kin": RelationType.next_of_kin_for,
    "medical_proxy": RelationType.healthcare_proxy_for,
    "power_of_attorney": RelationType.power_of_attorney_for,
    "introduced_to": RelationType.introduced,
    "introduced_by": RelationType.introduced_by,
    "custom": RelationType.knows,
}

_INVERSE: dict[RelationType, RelationType] = {
    RelationType.parent_of: RelationType.child_of,
    RelationType.child_of: RelationType.parent_of,
    RelationType.grandparent_of: RelationType.grandchild_of,
    RelationType.grandchild_of: RelationType.grandparent_of,
    RelationType.aunt_uncle_of: RelationType.niece_nephew_of,
    RelationType.niece_nephew_of: RelationType.aunt_uncle_of,
    RelationType.step_parent_of: RelationType.step_child_of,
    RelationType.step_child_of: RelationType.step_parent_of,
    RelationType.adopted_parent_of: RelationType.adopted_child_of,
    RelationType.adopted_child_of: RelationType.adopted_parent_of,
    RelationType.manager_of: RelationType.reports_to,
    RelationType.reports_to: RelationType.manager_of,
    RelationType.mentor_of: RelationType.mentee_of,
    RelationType.mentee_of: RelationType.mentor_of,
    RelationType.client_of: RelationType.vendor_of,
    RelationType.vendor_of: RelationType.client_of,
    RelationType.referrer_of: RelationType.referred_by,
    RelationType.referred_by: RelationType.referrer_of,
    RelationType.counsel_for: RelationType.client_of_counsel,
    RelationType.client_of_counsel: RelationType.counsel_for,
    RelationType.introduced: RelationType.introduced_by,
    RelationType.introduced_by: RelationType.introduced,
}

_FAMILIES: dict[str, set[RelationType]] = {
    "family": {
        RelationType.parent_of,
        RelationType.child_of,
        RelationType.sibling_of,
        RelationType.grandparent_of,
        RelationType.grandchild_of,
        RelationType.aunt_uncle_of,
        RelationType.niece_nephew_of,
        RelationType.cousin_of,
        RelationType.step_parent_of,
        RelationType.step_child_of,
        RelationType.step_sibling_of,
        RelationType.adopted_parent_of,
        RelationType.adopted_child_of,
    },
    "romantic": {
        RelationType.spouse_of,
        RelationType.partner_of,
        RelationType.ex_spouse_of,
        RelationType.dating,
        RelationType.engaged_to,
    },
    "social": {
        RelationType.friend_of,
        RelationType.close_friend_of,
        RelationType.acquaintance_of,
        RelationType.knows,
        RelationType.met_once,
        RelationType.roommate_of,
    },
    "professional": {
        RelationType.colleague_of,
        RelationType.manager_of,
        RelationType.reports_to,
        RelationType.mentor_of,
        RelationType.mentee_of,
        RelationType.co_founder_of,
        RelationType.client_of,
        RelationType.vendor_of,
    },
    "legal": {
        RelationType.counsel_for,
        RelationType.client_of_counsel,
        RelationType.witness_for,
        RelationType.expert_for,
    },
    "emergency": {
        RelationType.emergency_contact_for,
        RelationType.next_of_kin_for,
        RelationType.healthcare_proxy_for,
        RelationType.power_of_attorney_for,
    },
    "custom": {RelationType.knows},
}


def _rel(value: str) -> RelationType:
    if value in _RELATION_MAP:
        return _RELATION_MAP[value]
    try:
        return RelationType(value)
    except ValueError as exc:
        raise ToolError(VALIDATION_FAILED, f"unsupported relation_type: {value}") from exc


def _inverse(value: RelationType) -> RelationType:
    return _INVERSE.get(value, value)


async def link_relationship(ctx: MCPContext, args: BaseModel) -> RelationshipOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("relationship:write",))
    data = LinkRelationshipInput.model_validate(args)
    cached = await check_or_register(
        ctx,
        idempotency_key=str(data.idempotency_key) if data.idempotency_key else None,
        tool_name="link_relationship",
        stable_args_hash=stable_hash(data),
    )
    if cached is not None:
        return RelationshipOutput.model_validate(cached)
    if data.from_person_id == data.to_person_id:
        raise ToolError(
            VALIDATION_FAILED, "self relationships are blocked by current Phase 0 schema"
        )
    await load_person(ctx, data.from_person_id)
    await load_person(ctx, data.to_person_id)
    relation_type = _rel(data.relation_type)
    inverse_type = _inverse(relation_type)
    existing = await ctx.db.scalar(
        select(PersonPersonRelation).where(
            PersonPersonRelation.from_person_id == data.from_person_id,
            PersonPersonRelation.to_person_id == data.to_person_id,
            PersonPersonRelation.relation_type == relation_type,
            PersonPersonRelation.tenant_visibility == ctx.tenant_id,
        )
    )
    if existing is not None:
        raise ToolError(RELATIONSHIP_ALREADY_EXISTS, "relationship already exists", retryable=False)
    edge = PersonPersonRelation(
        from_person_id=data.from_person_id,
        to_person_id=data.to_person_id,
        relation_type=relation_type,
        inverse_relation_type=inverse_type,
        strength=data.strength,
        started_at=data.since.date() if data.since else None,
        ended_at=data.until.date() if data.until else None,
        context=data.context,
        confidence=data.confidence,
        tenant_visibility=ctx.tenant_id,
    )
    reverse = PersonPersonRelation(
        from_person_id=data.to_person_id,
        to_person_id=data.from_person_id,
        relation_type=inverse_type,
        inverse_relation_type=relation_type,
        strength=data.strength,
        started_at=edge.started_at,
        ended_at=edge.ended_at,
        context=data.context,
        confidence=data.confidence,
        tenant_visibility=ctx.tenant_id,
    )
    ctx.db.add_all([edge, reverse])
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="relationship.link",
        aggregate_type="edge",
        aggregate_id=edge.id,
        affected_ids=[data.from_person_id, data.to_person_id],
        payload_before=None,
        payload_after={
            "relation_type": relation_type.value,
            "inverse_relation_type": inverse_type.value,
        },
        confidence=data.confidence,
        evidence={
            "source_label": data.source_label,
            "case_id": str(data.case_id) if data.case_id else None,
            "request_id": ctx.request_id,
        },
    )
    output = RelationshipOutput(
        edge_id=edge.id,
        from_person_id=edge.from_person_id,
        to_person_id=edge.to_person_id,
        relation_type=edge.relation_type.value,
        inverse_relation_type=edge.inverse_relation_type.value,
        status="applied",
        event_id=event_id,
    )
    if data.idempotency_key:
        await store_result(ctx, str(data.idempotency_key), output.model_dump(mode="json"))
    return output


async def unlink_relationship(ctx: MCPContext, args: BaseModel) -> UnlinkRelationshipOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("relationship:write",))
    data = UnlinkRelationshipInput.model_validate(args)
    stmt = select(PersonPersonRelation).where(
        PersonPersonRelation.tenant_visibility == ctx.tenant_id
    )
    if data.edge_id:
        stmt = stmt.where(PersonPersonRelation.id == data.edge_id)
    elif data.from_person_id and data.to_person_id and data.relation_type:
        stmt = stmt.where(
            PersonPersonRelation.from_person_id == data.from_person_id,
            PersonPersonRelation.to_person_id == data.to_person_id,
            PersonPersonRelation.relation_type == _rel(data.relation_type),
        )
    else:
        raise ToolError(VALIDATION_FAILED, "edge_id or from/to/relation_type is required")
    edge = await ctx.db.scalar(stmt)
    if edge is None:
        raise ToolError(EDGE_NOT_FOUND, "relationship edge not found")
    ended_at = data.ended_at or datetime.now(tz=edge.observed_at.tzinfo)
    edge.ended_at = ended_at.date()
    await ctx.db.execute(
        sa.update(PersonPersonRelation)
        .where(
            PersonPersonRelation.from_person_id == edge.to_person_id,
            PersonPersonRelation.to_person_id == edge.from_person_id,
            PersonPersonRelation.relation_type == edge.inverse_relation_type,
            PersonPersonRelation.tenant_visibility == ctx.tenant_id,
        )
        .values(ended_at=edge.ended_at)
    )
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="relationship.unlink",
        aggregate_type="edge",
        aggregate_id=edge.id,
        affected_ids=[edge.from_person_id, edge.to_person_id],
        payload_before=None,
        payload_after={"ended_at": ended_at.isoformat(), "reason": data.reason},
        confidence=data.confidence,
    )
    return UnlinkRelationshipOutput(
        edge_id=edge.id, status="applied", ended_at=ended_at, event_id=event_id
    )


async def list_relationships(ctx: MCPContext, args: BaseModel) -> ListRelationshipsOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("relationship:read",))
    data = ListRelationshipsInput.model_validate(args)
    await load_person(ctx, data.person_id)
    stmt = select(PersonPersonRelation).where(
        PersonPersonRelation.tenant_visibility == ctx.tenant_id
    )
    if data.direction == "outgoing":
        stmt = stmt.where(PersonPersonRelation.from_person_id == data.person_id)
    elif data.direction == "incoming":
        stmt = stmt.where(PersonPersonRelation.to_person_id == data.person_id)
    else:
        stmt = stmt.where(
            or_(
                PersonPersonRelation.from_person_id == data.person_id,
                PersonPersonRelation.to_person_id == data.person_id,
            )
        )
    if data.active_only:
        stmt = stmt.where(
            or_(
                PersonPersonRelation.ended_at.is_(None),
                PersonPersonRelation.ended_at > date.today(),
            )
        )
    relation_filters: set[RelationType] = set()
    if data.type_family_any:
        for family in data.type_family_any:
            relation_filters.update(_FAMILIES[family])
    if data.relation_type_any:
        relation_filters.update(_rel(value) for value in data.relation_type_any)
    if relation_filters:
        stmt = stmt.where(PersonPersonRelation.relation_type.in_(relation_filters))
    result = await ctx.db.execute(
        stmt.order_by(PersonPersonRelation.created_at.desc()).limit(data.limit)
    )
    edges = result.scalars().all()
    items = []
    for edge in edges:
        other_id = (
            edge.to_person_id if edge.from_person_id == data.person_id else edge.from_person_id
        )
        other = await ctx.db.get(Person, other_id)
        if other is None:
            continue
        items.append(
            RelationItem(
                edge_id=edge.id,
                from_person_id=edge.from_person_id,
                to_person_id=edge.to_person_id,
                relation_type=edge.relation_type.value,
                since=edge.started_at,
                until=edge.ended_at,
                context=edge.context,
                other_person_summary=person_summary(other),
            )
        )
    return ListRelationshipsOutput(items=items, count=len(items), next_cursor=None)


async def suggest_relationships(ctx: MCPContext, args: BaseModel) -> SuggestRelationshipsOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("relationship:read", "proposal:read"))
    data = SuggestRelationshipsInput.model_validate(args)
    await load_person(ctx, data.person_id)
    return SuggestRelationshipsOutput(items=[], count=0)


async def bulk_link_relationships(ctx: MCPContext, args: BaseModel) -> BulkLinkRelationshipsOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("relationship:write", "person:bulk"))
    data = BulkLinkRelationshipsInput.model_validate(args)
    cached = await check_or_register(
        ctx,
        idempotency_key=str(data.idempotency_key) if data.idempotency_key else None,
        tool_name="bulk_link_relationships",
        stable_args_hash=stable_hash(data),
    )
    if cached is not None:
        return BulkLinkRelationshipsOutput.model_validate(cached)
    created = noop = errors = 0
    results: list[BulkLinkResult] = []
    for index, item in enumerate(data.items):
        try:
            item_output = await link_relationship(ctx, item)
            created += 1
            results.append(BulkLinkResult(index=index, action="created", edge_id=item_output.edge_id))
        except ToolError as exc:
            if exc.code == RELATIONSHIP_ALREADY_EXISTS:
                noop += 1
                results.append(BulkLinkResult(index=index, action="noop", error_code=exc.code))
            else:
                errors += 1
                results.append(BulkLinkResult(index=index, action="error", error_code=exc.code))
    batch_event_id = await emit_action_event(
        ctx,
        event_type="relationship.bulk_link",
        aggregate_type="edge",
        aggregate_id=uuid.uuid4(),
        payload_before=None,
        payload_after={"count": len(results)},
    )
    batch_output = BulkLinkRelationshipsOutput(
        created=created, noop=noop, errors=errors, results=results, batch_event_id=batch_event_id
    )
    if data.idempotency_key:
        await store_result(ctx, str(data.idempotency_key), batch_output.model_dump(mode="json"))
    return batch_output


def register_relationship_tools() -> None:
    base = {"openWorldHint": False}
    register(
        name="link_relationship",
        description="Assert a person-to-person relationship and deterministic inverse edge. STAFF with relationship:write required.",
        input_model=LinkRelationshipInput,
        output_model=RelationshipOutput,
        handler=link_relationship,
        required_role="STAFF",
        required_scopes=("relationship:write",),
        annotations={
            **base,
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        idempotency="idempotency-key",
    )
    register(
        name="unlink_relationship",
        description="Time-bound an existing relationship instead of deleting history. STAFF with relationship:write required.",
        input_model=UnlinkRelationshipInput,
        output_model=UnlinkRelationshipOutput,
        handler=unlink_relationship,
        required_role="STAFF",
        required_scopes=("relationship:write",),
        annotations={
            **base,
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        idempotency="natural-key",
    )
    register(
        name="list_relationships",
        description="List relationships around one person with direction, family, type, and active filters. CLIENT with relationship:read required.",
        input_model=ListRelationshipsInput,
        output_model=ListRelationshipsOutput,
        handler=list_relationships,
        required_role="CLIENT",
        required_scopes=("relationship:read",),
        annotations={
            **base,
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        idempotency="none",
    )
    register(
        name="suggest_relationships",
        description="Read-only placeholder for Phase 3 relationship inference agents. Returns no candidates in Phase 1; STAFF read scopes required.",
        input_model=SuggestRelationshipsInput,
        output_model=SuggestRelationshipsOutput,
        handler=suggest_relationships,
        required_role="STAFF",
        required_scopes=("relationship:read", "proposal:read"),
        annotations={
            **base,
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        idempotency="none",
    )
    register(
        name="bulk_link_relationships",
        description="Assert up to 50 relationship edges with per-item outcomes and a batch audit event. STAFF write and bulk scopes required.",
        input_model=BulkLinkRelationshipsInput,
        output_model=BulkLinkRelationshipsOutput,
        handler=bulk_link_relationships,
        required_role="STAFF",
        required_scopes=("relationship:write", "person:bulk"),
        annotations={
            **base,
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        idempotency="idempotency-key",
    )
