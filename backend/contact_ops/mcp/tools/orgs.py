from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import (
    AMBIGUOUS_MATCH,
    DUPLICATE_RECORD,
    ORG_NOT_FOUND,
    VALIDATION_FAILED,
    ToolError,
)
from contact_ops.mcp.idempotency import check_or_register, store_result
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, now_utc, register, stable_hash
from contact_ops.models import ActionEvent, Identifier, Organization, PersonOrgRole
from contact_ops.models.enums import MergeStatus, OrgKind, RetentionClass

ORG_KINDS = tuple(kind.value for kind in OrgKind)


class OrgRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    org_id: uuid.UUID
    legal_name: str
    display_name: str
    kind: str
    domain: str | None = None
    industry: str | None = None
    etag: str
    merge_status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateOrganizationInput(BaseModel):
    legal_name: str = Field(min_length=1, max_length=300)
    display_name: str | None = Field(default=None, max_length=200)
    kind: Literal[ORG_KINDS] = "company"  # type: ignore[valid-type]
    industry: str | None = Field(default=None, max_length=120)
    domain: str | None = Field(default=None, max_length=253)
    description: str | None = Field(default=None, max_length=4000)
    dba_names: list[str] = Field(default_factory=list, max_length=10)
    naics_codes: list[str] = Field(default_factory=list, max_length=5)
    sic_codes: list[str] = Field(default_factory=list, max_length=5)
    employee_count_estimate: int | None = Field(default=None, ge=0)
    employee_count_range: str | None = None
    annual_revenue_estimate: Decimal | None = Field(default=None, ge=0)
    annual_revenue_range: str | None = None
    founded_year: int | None = Field(default=None, ge=1500, le=2999)
    dissolved_year: int | None = Field(default=None, ge=1500, le=2999)
    ticker_symbol: str | None = Field(default=None, max_length=10)
    cik: str | None = Field(default=None, max_length=10)
    duns_number: str | None = Field(default=None, pattern=r"^\d{9}$")
    sam_uei: str | None = Field(default=None, pattern=r"^[A-Z0-9]{12}$")
    cage_code: str | None = Field(default=None, pattern=r"^[A-Z0-9]{5}$")
    linkedin_company_id: str | None = Field(default=None, max_length=80)
    crunchbase_uuid: uuid.UUID | None = None
    parent_org_id: uuid.UUID | None = None
    tax_status: str | None = None
    state_of_incorporation: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    is_sdvosb: bool = False
    is_minority_owned: bool = False
    is_woman_owned: bool = False
    idempotency_key: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower().removeprefix("https://").removeprefix("http://")
        normalized = normalized.split("/", 1)[0].removeprefix("www.")
        if "." not in normalized or " " in normalized:
            raise ValueError("domain must be a DNS host name")
        return normalized


class CreateOrganizationOutput(ToolOutput):
    org_id: uuid.UUID
    etag: str
    display_name: str
    kind: str
    created_at: datetime | None
    status: str
    event_id: uuid.UUID | None = None


class UpsertOrganizationInput(CreateOrganizationInput):
    match_on: Literal["domain", "linkedin_company_id", "duns_number", "sam_uei"]
    match_value: str = Field(min_length=1, max_length=253)
    conflict_strategy: Literal["fill_blanks", "prefer_incoming", "prefer_existing"] = "fill_blanks"


class UpsertOrganizationOutput(ToolOutput):
    org_id: uuid.UUID
    action: Literal["created", "updated", "noop"]
    changed_fields: list[str]
    etag: str
    status: str
    event_id: uuid.UUID | None = None


class GetOrganizationInput(BaseModel):
    org_id: uuid.UUID


class GetOrganizationOutput(ToolOutput):
    organization: dict[str, Any]
    employee_count: int
    recent_action_events: list[dict[str, Any]]


class UpdateOrganizationInput(BaseModel):
    org_id: uuid.UUID
    etag: str
    legal_name: str | None = Field(default=None, min_length=1, max_length=300)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal[ORG_KINDS] | None = None  # type: ignore[valid-type]
    industry: str | None = Field(default=None, max_length=120)
    domain: str | None = Field(default=None, max_length=253)
    description: str | None = Field(default=None, max_length=4000)
    employee_count_estimate: int | None = Field(default=None, ge=0)
    employee_count_range: str | None = None
    founded_year: int | None = Field(default=None, ge=1500, le=2999)
    ticker_symbol: str | None = Field(default=None, max_length=10)
    cik: str | None = Field(default=None, max_length=10)
    duns_number: str | None = Field(default=None, pattern=r"^\d{9}$")
    sam_uei: str | None = Field(default=None, pattern=r"^[A-Z0-9]{12}$")
    linkedin_company_id: str | None = Field(default=None, max_length=80)
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdateOrganizationOutput(ToolOutput):
    org_id: uuid.UUID
    etag: str
    changed_fields: list[str]
    status: str
    event_id: uuid.UUID | None = None


class ArchiveOrganizationInput(BaseModel):
    org_id: uuid.UUID
    reason: Literal["dissolved", "acquired", "merged", "renamed", "test_data", "duplicate", "other"]
    reason_note: str | None = Field(default=None, max_length=1000)
    dissolved_year: int | None = Field(default=None, ge=1500, le=2999)
    confidence: float = Field(default=1.0, ge=0, le=1)


class ArchiveOrganizationOutput(ToolOutput):
    org_id: uuid.UUID
    archived_at: datetime
    status: str
    event_id: uuid.UUID | None = None
    undo_until: None = None


class ListOrganizationsInput(BaseModel):
    kind: str | None = None
    industry: str | None = None
    has_domain: bool | None = None
    include_archived: bool = False
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None


class ListOrganizationsOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int
    next_cursor: str | None = None
    total_count: int | None = None


class SearchOrganizationsInput(ListOrganizationsInput):
    query: str | None = Field(default=None, min_length=1, max_length=500)


class IdentifierProbe(BaseModel):
    namespace: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=400)


class FindOrganizationByIdentifierInput(BaseModel):
    identifiers: list[IdentifierProbe] = Field(min_length=1, max_length=10)
    match_mode: Literal["all_must_match", "any_can_match"] = "any_can_match"


class FindOrganizationByIdentifierOutput(ToolOutput):
    matches: list[dict[str, Any]]
    ambiguous: bool


def _org_to_record(org: Organization) -> OrgRecord:
    return OrgRecord(
        org_id=org.id,
        legal_name=org.legal_name,
        display_name=org.display_name,
        kind=org.kind.value,
        domain=org.domain,
        industry=org.industry,
        etag=org.etag,
        merge_status=org.merge_status.value,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


async def _load_org(ctx: MCPContext, org_id: uuid.UUID) -> Organization:
    org = await ctx.db.get(Organization, org_id)
    if org is None or org.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(
            ORG_NOT_FOUND, f"no organization with id {org_id}", hint="use search_organizations"
        )
    return org


async def create_organization(
    ctx: MCPContext, req: CreateOrganizationInput
) -> CreateOrganizationOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["org:write"])
    cached = await check_or_register(
        ctx,
        idempotency_key=req.idempotency_key,
        tool_name="create_organization",
        stable_args_hash=stable_hash(req),
    )
    if cached is not None:
        return CreateOrganizationOutput.model_validate(cached)

    if req.domain:
        existing = await ctx.db.scalar(
            select(Organization.id).where(
                Organization.canonical_owner_tenant_id == ctx.tenant_id,
                func.lower(Organization.domain) == req.domain.lower(),
            )
        )
        if existing:
            raise ToolError(
                DUPLICATE_RECORD,
                "organization domain already exists",
                hint="use upsert_organization",
            )

    org = Organization(
        # Organization.id has no model/Python default (only a DB server default the
        # ORM doesn't fetch), so set it explicitly like create_person does — else
        # the flush fails with "NULL identity key".
        id=uuid.uuid4(),
        legal_name=req.legal_name,
        display_name=req.display_name or req.legal_name,
        kind=OrgKind(req.kind),
        industry=req.industry,
        domain=req.domain,
        description=req.description,
        dba_names=req.dba_names,
        naics_codes=req.naics_codes,
        sic_codes=req.sic_codes,
        employee_count_estimate=req.employee_count_estimate,
        employee_count_range=req.employee_count_range,
        annual_revenue_estimate=req.annual_revenue_estimate,
        annual_revenue_range=req.annual_revenue_range,
        founded_year=req.founded_year,
        dissolved_year=req.dissolved_year,
        ticker_symbol=req.ticker_symbol,
        cik=req.cik,
        duns_number=req.duns_number,
        sam_uei=req.sam_uei,
        cage_code=req.cage_code,
        linkedin_company_id=req.linkedin_company_id,
        crunchbase_uuid=req.crunchbase_uuid,
        parent_org_id=req.parent_org_id,
        tax_status=req.tax_status,
        state_of_incorporation=req.state_of_incorporation,
        is_sdvosb=req.is_sdvosb,
        is_minority_owned=req.is_minority_owned,
        is_woman_owned=req.is_woman_owned,
        canonical_owner_tenant_id=ctx.tenant_id,
        retention_class=RetentionClass.operational_2y,
    )
    ctx.db.add(org)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="organization.create",
        aggregate_type="organization",
        aggregate_id=org.id,
        affected_ids=[],
        payload_before=None,
        payload_after=_org_to_record(org).model_dump(mode="json"),
        confidence=req.confidence,
    )
    result = CreateOrganizationOutput(
        org_id=org.id,
        etag=org.etag,
        display_name=org.display_name,
        kind=org.kind.value,
        created_at=org.created_at,
        status="applied",
        event_id=event_id,
    )
    if req.idempotency_key:
        await store_result(ctx, req.idempotency_key, result.model_dump(mode="json"))
    return result


async def upsert_organization(
    ctx: MCPContext, req: UpsertOrganizationInput
) -> UpsertOrganizationOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["org:write"])
    lookup_value = req.match_value.strip().lower()
    if req.match_on == "domain":
        lookup_value = (
            CreateOrganizationInput(legal_name=req.legal_name, domain=lookup_value).domain
            or lookup_value
        )
    existing = await ctx.db.scalar(
        select(Organization).where(
            Organization.canonical_owner_tenant_id == ctx.tenant_id,
            func.lower(getattr(Organization, req.match_on)) == lookup_value,
        )
    )
    if existing is None:
        created = await create_organization(ctx, req)
        return UpsertOrganizationOutput(
            org_id=created.org_id,
            action="created",
            changed_fields=["*"],
            etag=created.etag,
            status=created.status,
            event_id=created.event_id,
        )

    before = _org_to_record(existing).model_dump(mode="json")
    changed: list[str] = []
    if req.conflict_strategy != "prefer_existing":
        for field in (
            "legal_name",
            "display_name",
            "industry",
            "description",
            "employee_count_estimate",
            "employee_count_range",
            "founded_year",
            "ticker_symbol",
            "cik",
            "duns_number",
            "sam_uei",
            "linkedin_company_id",
        ):
            incoming = getattr(req, field)
            if incoming is None:
                continue
            current = getattr(existing, field)
            should_update = req.conflict_strategy == "prefer_incoming" or current in (None, "", [])
            if should_update and current != incoming:
                setattr(existing, field, incoming)
                changed.append(field)
    if not changed:
        return UpsertOrganizationOutput(
            org_id=existing.id,
            action="noop",
            changed_fields=[],
            etag=existing.etag,
            status="applied",
        )
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="organization.upsert",
        aggregate_type="organization",
        aggregate_id=existing.id,
        payload_before=before,
        payload_after=_org_to_record(existing).model_dump(mode="json"),
        confidence=req.confidence,
    )
    return UpsertOrganizationOutput(
        org_id=existing.id,
        action="updated",
        changed_fields=changed,
        etag=existing.etag,
        status="applied",
        event_id=event_id,
    )


async def get_organization(ctx: MCPContext, req: GetOrganizationInput) -> GetOrganizationOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["org:read"])
    org = await _load_org(ctx, req.org_id)
    employee_count = await ctx.db.scalar(
        select(func.count())
        .select_from(PersonOrgRole)
        .where(PersonOrgRole.organization_id == org.id)
    )
    events = (
        await ctx.db.execute(
            select(ActionEvent)
            .where(ActionEvent.aggregate_id == org.id)
            .order_by(ActionEvent.proposed_at.desc())
            .limit(10)
        )
    ).scalars()
    return GetOrganizationOutput(
        organization=_org_to_record(org).model_dump(mode="json"),
        employee_count=int(employee_count or 0),
        recent_action_events=[
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "status": event.status.value,
                "proposed_at": event.proposed_at,
            }
            for event in events
        ],
    )


async def update_organization(
    ctx: MCPContext, req: UpdateOrganizationInput
) -> UpdateOrganizationOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["org:write"])
    org = await _load_org(ctx, req.org_id)
    if org.etag != req.etag:
        raise ToolError(
            VALIDATION_FAILED, "stale organization etag", hint="reload organization before patching"
        )
    before = _org_to_record(org).model_dump(mode="json")
    changed: list[str] = []
    patch = req.model_dump(exclude={"org_id", "etag", "confidence"}, exclude_unset=True)
    for field, value in patch.items():
        if field == "kind" and value is not None:
            value = OrgKind(value)
        if getattr(org, field) != value:
            setattr(org, field, value)
            changed.append(field)
    if not changed:
        return UpdateOrganizationOutput(
            org_id=org.id, etag=org.etag, changed_fields=[], status="applied"
        )
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="organization.update",
        aggregate_type="organization",
        aggregate_id=org.id,
        payload_before=before,
        payload_after=_org_to_record(org).model_dump(mode="json"),
        confidence=req.confidence,
    )
    return UpdateOrganizationOutput(
        org_id=org.id,
        etag=org.etag,
        changed_fields=changed,
        status="applied",
        event_id=event_id,
    )


async def archive_organization(
    ctx: MCPContext, req: ArchiveOrganizationInput
) -> ArchiveOrganizationOutput:
    require_role(ctx, "MANAGER")
    require_scopes(ctx, ["org:archive"])
    org = await _load_org(ctx, req.org_id)
    before = _org_to_record(org).model_dump(mode="json")
    org.merge_status = MergeStatus.archived
    if req.dissolved_year is not None:
        org.dissolved_year = req.dissolved_year
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="organization.archive",
        aggregate_type="organization",
        aggregate_id=org.id,
        payload_before=before,
        payload_after={
            "reason": req.reason,
            "reason_note": req.reason_note,
            **_org_to_record(org).model_dump(mode="json"),
        },
        confidence=req.confidence,
    )
    return ArchiveOrganizationOutput(
        org_id=org.id,
        archived_at=now_utc(),
        status="applied",
        event_id=event_id,
    )


async def list_organizations(
    ctx: MCPContext, req: ListOrganizationsInput
) -> ListOrganizationsOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["org:read"])
    stmt = select(Organization).where(Organization.canonical_owner_tenant_id == ctx.tenant_id)
    if not req.include_archived:
        stmt = stmt.where(Organization.merge_status == MergeStatus.canonical)
    if req.kind:
        stmt = stmt.where(Organization.kind == OrgKind(req.kind))
    if req.industry:
        stmt = stmt.where(Organization.industry == req.industry)
    if req.has_domain is True:
        stmt = stmt.where(Organization.domain.is_not(None))
    if req.has_domain is False:
        stmt = stmt.where(Organization.domain.is_(None))
    if req.cursor:
        stmt = stmt.where(Organization.display_name > req.cursor)
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        (await ctx.db.execute(stmt.order_by(Organization.display_name).limit(req.limit + 1)))
        .scalars()
        .all()
    )
    items = rows[: req.limit]
    return ListOrganizationsOutput(
        items=[_org_to_record(org).model_dump(mode="json") for org in items],
        count=len(items),
        total_count=int(total or 0),
        next_cursor=items[-1].display_name if len(rows) > req.limit and items else None,
    )


async def search_organizations(
    ctx: MCPContext, req: SearchOrganizationsInput
) -> ListOrganizationsOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["org:read"])
    stmt = select(Organization).where(Organization.canonical_owner_tenant_id == ctx.tenant_id)
    if not req.include_archived:
        stmt = stmt.where(Organization.merge_status == MergeStatus.canonical)
    if req.query:
        q = f"%{req.query.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Organization.display_name).like(q),
                func.lower(Organization.legal_name).like(q),
                func.lower(Organization.domain).like(q),
                func.lower(Organization.industry).like(q),
            )
        )
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        (await ctx.db.execute(stmt.order_by(Organization.display_name).limit(req.limit + 1)))
        .scalars()
        .all()
    )
    items = rows[: req.limit]
    return ListOrganizationsOutput(
        items=[_org_to_record(org).model_dump(mode="json") for org in items],
        count=len(items),
        total_count=int(total or 0),
        next_cursor=items[-1].display_name if len(rows) > req.limit and items else None,
    )


async def find_organization_by_identifier(
    ctx: MCPContext, req: FindOrganizationByIdentifierInput
) -> FindOrganizationByIdentifierOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["org:read"])
    matched_ids: set[uuid.UUID] | None = None
    matched_on: dict[uuid.UUID, list[str]] = {}
    for probe in req.identifiers:
        ns = probe.namespace.strip().lower()
        val = probe.value.strip().lower()
        direct_column = {
            "domain": Organization.domain,
            "linkedin_company_id": Organization.linkedin_company_id,
            "duns": Organization.duns_number,
            "duns_number": Organization.duns_number,
            "sam_uei": Organization.sam_uei,
            "cik": Organization.cik,
            "ticker": Organization.ticker_symbol,
        }.get(ns)
        ids: set[uuid.UUID] = set()
        if direct_column is not None:
            ids.update(
                (
                    await ctx.db.execute(
                        select(Organization.id).where(
                            Organization.canonical_owner_tenant_id == ctx.tenant_id,
                            func.lower(direct_column) == val,
                        )
                    )
                ).scalars()
            )
        identifier_ids = (
            (
                await ctx.db.execute(
                    select(Identifier.organization_id).where(
                        Identifier.organization_id.is_not(None),
                        func.lower(Identifier.namespace) == ns,
                        func.lower(Identifier.value) == val,
                    )
                )
            )
            .scalars()
            .all()
        )
        ids.update(org_id for org_id in identifier_ids if org_id is not None)
        for org_id in ids:
            matched_on.setdefault(org_id, []).append(f"{probe.namespace}:{probe.value}")
        matched_ids = (
            ids
            if matched_ids is None
            else (matched_ids & ids if req.match_mode == "all_must_match" else matched_ids | ids)
        )
    final_ids = matched_ids or set()
    orgs = (
        (await ctx.db.execute(select(Organization).where(Organization.id.in_(final_ids))))
        .scalars()
        .all()
    )
    matches = [
        {
            "org_id": org.id,
            "display_name": org.display_name,
            "matched_on": matched_on.get(org.id, []),
            "etag": org.etag,
        }
        for org in orgs
    ]
    if len(matches) > 1:
        raise ToolError(
            AMBIGUOUS_MATCH,
            "identifier matched multiple organizations",
            hint="use returned candidates to disambiguate",
        )
    return FindOrganizationByIdentifierOutput(matches=matches, ambiguous=False)


register(
    name="create_organization",
    description=(
        "Create a tenant-scoped organization record with rich company identifiers. "
        "Requires STAFF and org:write; use upsert_organization when a natural key may exist."
    ),
    input_model=CreateOrganizationInput,
    output_model=CreateOrganizationOutput,
    handler=create_organization,
    required_role="STAFF",
    required_scopes=("org:write",),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    idempotency="idempotency-key",
)
register(
    name="upsert_organization",
    description=(
        "Create or patch an organization by a natural identifier such as domain, "
        "LinkedIn company id, DUNS, or SAM UEI. Requires STAFF and org:write."
    ),
    input_model=UpsertOrganizationInput,
    output_model=UpsertOrganizationOutput,
    handler=upsert_organization,
    required_role="STAFF",
    required_scopes=("org:write",),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="natural-key",
)
register(
    name="get_organization",
    description=(
        "Return a single organization with employee count and recent action events "
        "for the current tenant. Requires CLIENT and org:read."
    ),
    input_model=GetOrganizationInput,
    output_model=GetOrganizationOutput,
    handler=get_organization,
    required_role="CLIENT",
    required_scopes=("org:read",),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="none",
)
register(
    name="update_organization",
    description=(
        "Patch mutable organization fields using etag optimistic concurrency. "
        "Requires STAFF and org:write."
    ),
    input_model=UpdateOrganizationInput,
    output_model=UpdateOrganizationOutput,
    handler=update_organization,
    required_role="STAFF",
    required_scopes=("org:write",),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="none",
)
register(
    name="archive_organization",
    description=(
        "Soft-archive an organization without ending employee history. "
        "Requires MANAGER and org:archive."
    ),
    input_model=ArchiveOrganizationInput,
    output_model=ArchiveOrganizationOutput,
    handler=archive_organization,
    required_role="MANAGER",
    required_scopes=("org:archive",),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="natural-key",
)
register(
    name="list_organizations",
    description=(
        "List tenant-visible organizations with narrow filters and cursor pagination. "
        "Requires CLIENT and org:read."
    ),
    input_model=ListOrganizationsInput,
    output_model=ListOrganizationsOutput,
    handler=list_organizations,
    required_role="CLIENT",
    required_scopes=("org:read",),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="none",
)
register(
    name="search_organizations",
    description=(
        "Search tenant-visible organizations by free text and filters. "
        "Requires CLIENT and org:read."
    ),
    input_model=SearchOrganizationsInput,
    output_model=ListOrganizationsOutput,
    handler=search_organizations,
    required_role="CLIENT",
    required_scopes=("org:read",),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="none",
)
register(
    name="find_organization_by_identifier",
    description=(
        "Find an organization by exact natural identifiers such as domain, DUNS, "
        "SAM UEI, ticker, or identifier rows. Requires CLIENT and org:read."
    ),
    input_model=FindOrganizationByIdentifierInput,
    output_model=FindOrganizationByIdentifierOutput,
    handler=find_organization_by_identifier,
    required_role="CLIENT",
    required_scopes=("org:read",),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="none",
)
