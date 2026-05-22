from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import ORG_NOT_FOUND, PERSON_NOT_FOUND, VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, register
from contact_ops.mcp.tools.orgs import _load_org
from contact_ops.models import Organization, Person, PersonOrgRole
from contact_ops.models.enums import EmploymentType, RoleType, SeniorityLevel


class EmploymentRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employment_id: uuid.UUID
    person_id: uuid.UUID
    org_id: uuid.UUID
    role_type: str
    title: str | None = None
    department: str | None = None
    seniority: str
    employment_type: str | None = None
    is_primary: bool
    started_at: date | None = None
    ended_at: date | None = None
    ownership_percent: Decimal | None = None
    equity_class: str | None = None
    etag: str


class SetEmploymentInput(BaseModel):
    person_id: uuid.UUID
    org_id: uuid.UUID
    role_type: str
    title: str | None = Field(default=None, max_length=200)
    department: str | None = Field(default=None, max_length=120)
    seniority: str = "unknown"
    employment_type: str | None = None
    started_at: date | None = None
    ended_at: date | None = None
    is_primary: bool = False
    ownership_percent: Decimal | None = Field(default=None, ge=0, le=100)
    equity_class: str | None = Field(default=None, max_length=40)
    confidence: float = Field(default=1.0, ge=0, le=1)


class SetEmploymentOutput(ToolOutput):
    employment_id: uuid.UUID
    person_id: uuid.UUID
    org_id: uuid.UUID
    status: str
    event_id: uuid.UUID | None = None


class EndEmploymentInput(BaseModel):
    employment_id: uuid.UUID
    ended_at: date = Field(default_factory=date.today)
    reason: Literal["resigned", "laid_off", "fired", "retired", "contract_ended", "unknown"] = (
        "unknown"
    )
    confidence: float = Field(default=1.0, ge=0, le=1)


class EndEmploymentOutput(ToolOutput):
    employment_id: uuid.UUID
    ended_at: date
    status: str
    event_id: uuid.UUID | None = None


class UpdateEmploymentInput(BaseModel):
    employment_id: uuid.UUID
    etag: str
    title: str | None = Field(default=None, max_length=200)
    department: str | None = Field(default=None, max_length=120)
    seniority: str | None = None
    employment_type: str | None = None
    started_at: date | None = None
    ended_at: date | None = None
    ownership_percent: Decimal | None = Field(default=None, ge=0, le=100)
    equity_class: str | None = Field(default=None, max_length=40)
    is_primary: bool | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdateEmploymentOutput(ToolOutput):
    employment_id: uuid.UUID
    etag: str
    changed_fields: list[str]
    status: str
    event_id: uuid.UUID | None = None


class ListEmploymentsInput(BaseModel):
    person_id: uuid.UUID | None = None
    org_id: uuid.UUID | None = None
    active_only: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None


class ListEmploymentsOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int
    next_cursor: str | None = None


def _etag(row: PersonOrgRole) -> str:
    return row.updated_at.isoformat() if row.updated_at else str(row.id)


def _record(row: PersonOrgRole) -> EmploymentRecord:
    return EmploymentRecord(
        employment_id=row.id,
        person_id=row.person_id,
        org_id=row.organization_id,
        role_type=row.role_type.value,
        title=row.title,
        department=row.department,
        seniority=row.seniority.value,
        employment_type=row.employment_type.value if row.employment_type else None,
        is_primary=row.is_primary,
        started_at=row.started_at,
        ended_at=row.ended_at,
        ownership_percent=row.ownership_percent,
        equity_class=row.equity_class,
        etag=_etag(row),
    )


async def _load_employment(ctx: MCPContext, employment_id: uuid.UUID) -> PersonOrgRole:
    row = await ctx.db.get(PersonOrgRole, employment_id)
    if row is None:
        raise ToolError(VALIDATION_FAILED, "employment not found")
    person = await ctx.db.get(Person, row.person_id)
    if person is None or person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PERSON_NOT_FOUND, "person not found")
    return row


async def _demote_other_primary(
    ctx: MCPContext, person_id: uuid.UUID, keep_id: uuid.UUID | None = None
) -> None:
    rows = (
        await ctx.db.execute(
            select(PersonOrgRole).where(
                PersonOrgRole.person_id == person_id,
                PersonOrgRole.is_primary.is_(True),
                PersonOrgRole.ended_at.is_(None),
            )
        )
    ).scalars()
    for row in rows:
        if keep_id is None or row.id != keep_id:
            row.is_primary = False


async def set_employment(ctx: MCPContext, req: SetEmploymentInput) -> SetEmploymentOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["org:write", "person:write"])
    await load_person(ctx, req.person_id)
    org = await ctx.db.get(Organization, req.org_id)
    if org is None or org.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(ORG_NOT_FOUND, "organization not found")
    if req.ownership_percent is not None and req.role_type not in {
        "founder",
        "co_founder",
        "investor",
        "lead_investor",
        "angel",
        "board_member",
        "beneficial_owner",
        "owner",
    }:
        raise ToolError(VALIDATION_FAILED, "ownership_percent is not valid for this role")
    if req.is_primary:
        await _demote_other_primary(ctx, req.person_id)
    row = PersonOrgRole(
        person_id=req.person_id,
        organization_id=req.org_id,
        role_type=RoleType(req.role_type),
        title=req.title,
        department=req.department,
        seniority=SeniorityLevel(req.seniority),
        employment_type=EmploymentType(req.employment_type) if req.employment_type else None,
        started_at=req.started_at,
        ended_at=req.ended_at,
        is_primary=req.is_primary,
        ownership_percent=req.ownership_percent,
        equity_class=req.equity_class,
        confidence=Decimal(str(req.confidence)),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    if req.is_primary and req.ended_at is None:
        person = await ctx.db.get(Person, req.person_id)
        if person is not None:
            person.current_org_id = req.org_id
            person.current_org_role_id = row.id
    event_id = await emit_action_event(
        ctx,
        event_type="employment.set",
        aggregate_type="edge",
        aggregate_id=row.id,
        affected_ids=[req.person_id, req.org_id],
        payload_before=None,
        payload_after=_record(row).model_dump(mode="json"),
        confidence=req.confidence,
    )
    return SetEmploymentOutput(
        employment_id=row.id,
        person_id=req.person_id,
        org_id=req.org_id,
        status="applied",
        event_id=event_id,
    )


async def end_employment(ctx: MCPContext, req: EndEmploymentInput) -> EndEmploymentOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["org:write", "person:write"])
    row = await _load_employment(ctx, req.employment_id)
    if row.ended_at is not None:
        return EndEmploymentOutput(employment_id=row.id, ended_at=row.ended_at, status="applied")
    before = _record(row).model_dump(mode="json")
    row.ended_at = req.ended_at
    was_primary = row.is_primary
    row.is_primary = False
    person = await ctx.db.get(Person, row.person_id)
    if was_primary and person is not None:
        person.current_org_id = None
        person.current_org_role_id = None
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="employment.end",
        aggregate_type="edge",
        aggregate_id=row.id,
        affected_ids=[row.person_id, row.organization_id],
        payload_before=before,
        payload_after={**_record(row).model_dump(mode="json"), "reason": req.reason},
        confidence=req.confidence,
    )
    return EndEmploymentOutput(
        employment_id=row.id, ended_at=req.ended_at, status="applied", event_id=event_id
    )


async def update_employment(ctx: MCPContext, req: UpdateEmploymentInput) -> UpdateEmploymentOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["org:write", "person:write"])
    row = await _load_employment(ctx, req.employment_id)
    if _etag(row) != req.etag:
        raise ToolError(VALIDATION_FAILED, "stale employment etag")
    before = _record(row).model_dump(mode="json")
    changed: list[str] = []
    patch = req.model_dump(exclude={"employment_id", "etag", "confidence"}, exclude_unset=True)
    for field, value in patch.items():
        if field == "role_type" and value is not None:
            value = RoleType(value)
        elif field == "seniority" and value is not None:
            value = SeniorityLevel(value)
        elif field == "employment_type" and value is not None:
            value = EmploymentType(value)
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed.append(field)
    if req.is_primary is True:
        await _demote_other_primary(ctx, row.person_id, row.id)
    await ctx.db.flush()
    event_id = None
    if changed:
        event_id = await emit_action_event(
            ctx,
            event_type="employment.update",
            aggregate_type="edge",
            aggregate_id=row.id,
            affected_ids=[row.person_id, row.organization_id],
            payload_before=before,
            payload_after=_record(row).model_dump(mode="json"),
            confidence=req.confidence,
        )
    return UpdateEmploymentOutput(
        employment_id=row.id,
        etag=_etag(row),
        changed_fields=changed,
        status="applied",
        event_id=event_id,
    )


async def list_employments(ctx: MCPContext, req: ListEmploymentsInput) -> ListEmploymentsOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["org:read", "person:read"])
    if req.person_id is None and req.org_id is None:
        raise ToolError(VALIDATION_FAILED, "at least one of person_id or org_id is required")
    stmt = select(PersonOrgRole)
    if req.person_id is not None:
        await load_person(ctx, req.person_id)
        stmt = stmt.where(PersonOrgRole.person_id == req.person_id)
    if req.org_id is not None:
        await _load_org(ctx, req.org_id)
        stmt = stmt.where(PersonOrgRole.organization_id == req.org_id)
    if req.active_only:
        stmt = stmt.where(PersonOrgRole.ended_at.is_(None))
    if req.cursor:
        stmt = stmt.where(PersonOrgRole.id > uuid.UUID(req.cursor))
    rows = (
        (
            await ctx.db.execute(
                stmt.order_by(PersonOrgRole.started_at.desc().nullslast()).limit(req.limit + 1)
            )
        )
        .scalars()
        .all()
    )
    items = rows[: req.limit]
    return ListEmploymentsOutput(
        items=[_record(row).model_dump(mode="json") for row in items],
        count=len(items),
        next_cursor=str(items[-1].id) if len(rows) > req.limit and items else None,
    )


register(
    name="set_employment",
    description=(
        "Create a time-bounded person to organization role, optionally primary. "
        "Requires STAFF plus org:write and person:write."
    ),
    input_model=SetEmploymentInput,
    output_model=SetEmploymentOutput,
    handler=set_employment,
    required_role="STAFF",
    required_scopes=("org:write", "person:write"),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="natural-key",
)
register(
    name="end_employment",
    description=(
        "Set an end date on an existing employment record and clear primary/current "
        "projections when needed. Requires STAFF."
    ),
    input_model=EndEmploymentInput,
    output_model=EndEmploymentOutput,
    handler=end_employment,
    required_role="STAFF",
    required_scopes=("org:write", "person:write"),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="natural-key",
)
register(
    name="list_employments",
    description=(
        "List current and historical employment records by person, organization, or both. "
        "Requires CLIENT read scopes."
    ),
    input_model=ListEmploymentsInput,
    output_model=ListEmploymentsOutput,
    handler=list_employments,
    required_role="CLIENT",
    required_scopes=("org:read", "person:read"),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="none",
)
register(
    name="update_employment",
    description=(
        "Patch an employment record with optimistic etag checking, including promotion "
        "and title changes. Requires STAFF."
    ),
    input_model=UpdateEmploymentInput,
    output_model=UpdateEmploymentOutput,
    handler=update_employment,
    required_role="STAFF",
    required_scopes=("org:write", "person:write"),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="none",
)
