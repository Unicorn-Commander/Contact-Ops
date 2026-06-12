"""People MCP tools."""
# ruff: noqa: E501,I001

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, cast

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import Select, func, or_, select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import (
    AMBIGUOUS_MATCH,
    CONFIRMATION_PHRASE_MISMATCH,
    DEPENDENCY_VIOLATION,
    NO_FILTER_PROVIDED,
    STALE_ETAG,
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
    now_utc,
    person_summary,
    primary_email_for,
    register,
    stable_hash,
)
from contact_ops.models import (
    ActionEvent,
    Email,
    MergeCandidate,
    Person,
    PersonTenantMembership,
    Phone,
)
from contact_ops.models.enums import (
    EmailType,
    MergeStatus,
    PersonKind,
    PhoneType,
    PreferredContact,
    TemperatureState,
)


class Birthday(BaseModel):
    year: int | None = Field(default=None, ge=1850, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)


class EmailInput(BaseModel):
    address: EmailStr
    type: Literal["work", "home", "personal", "other"] = "other"
    is_primary: bool = False


class PhoneInput(BaseModel):
    e164: str = Field(pattern=r"^\+[1-9]\d{1,14}$")
    type: Literal["mobile", "work", "home", "fax", "other"] = "mobile"
    is_primary: bool = False


class IdentifierInput(BaseModel):
    namespace: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=400)


class CreatePersonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    kind: Literal["individual", "group", "org", "location"] = "individual"
    given_name: str | None = Field(default=None, max_length=100)
    family_name: str | None = Field(default=None, max_length=100)
    additional_names: list[str] = Field(default_factory=list, max_length=5)
    honorific_prefix: str | None = Field(default=None, max_length=40)
    honorific_suffix: str | None = Field(default=None, max_length=40)
    nicknames: list[str] = Field(default_factory=list, max_length=10)
    pronouns: str | None = Field(default=None, max_length=40)
    gender_identity: str | None = Field(default=None, max_length=120)
    birthday: Birthday | None = None
    preferred_languages: list[str] = Field(default_factory=list, max_length=5)
    time_zone: str | None = Field(default=None, max_length=60)
    preferred_contact_channel: (
        Literal["email", "phone", "sms", "signal", "imessage", "linkedin", "other"] | None
    ) = None
    headline: str | None = Field(default=None, max_length=200)
    bio: str | None = Field(default=None, max_length=4000)
    occupation_title: str | None = Field(default=None, max_length=120)
    emails: list[EmailInput] = Field(default_factory=list, max_length=20)
    phones: list[PhoneInput] = Field(default_factory=list, max_length=20)
    initial_tags: list[str] = Field(default_factory=list, max_length=20)
    tenant_membership_notes: str | None = Field(default=None, max_length=2000)
    idempotency_key: uuid.UUID | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("preferred_languages")
    @classmethod
    def validate_languages(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 8 or not item[:2].isalpha():
                raise ValueError("preferred_languages must be BCP-47-like tags")
        return value


class CreatePersonOutput(ToolOutput):
    person_id: uuid.UUID
    etag: str
    vcard_uid: str
    display_name: str
    kind: str
    created_at: datetime | None
    status: Literal["applied", "proposed"]
    event_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None


class UpsertPersonInput(CreatePersonInput):
    match_by: list[IdentifierInput] = Field(min_length=1, max_length=5)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)  # type: ignore[assignment]
    conflict_strategy: Literal[
        "fill_blanks", "prefer_incoming", "prefer_existing", "propose_only"
    ] = "fill_blanks"
    confidence: float = Field(default=0.9, ge=0, le=1)


class UpsertPersonOutput(ToolOutput):
    person_id: uuid.UUID
    action: Literal["created", "updated", "noop"]
    changed_fields: list[str]
    etag: str
    status: Literal["applied", "proposed"]
    event_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None


class GetPersonInput(BaseModel):
    person_id: uuid.UUID
    include: list[str] = Field(default_factory=list, max_length=15)
    latest_interactions_limit: int = Field(default=5, ge=1, le=50)
    as_of: datetime | None = None


class GetPersonOutput(ToolOutput):
    person_id: uuid.UUID
    etag: str
    vcard_uid: str | None
    display_name: str
    kind: str
    status: str
    merged_into_id: uuid.UUID | None = None
    emails: list[dict[str, Any]] | None = None
    phones: list[dict[str, Any]] | None = None
    # The owner's personal relationship to this contact (friend, coworker,
    # family, client, …). Held per-tenant in person_tenant_membership.custom_attrs
    # so it's the *caller's* view, not a shared fact about the person.
    relationship_category: str | None = None
    recent_events: list[dict[str, Any]]


class SetRelationshipCategoryInput(BaseModel):
    person_id: uuid.UUID
    # The owner's relationship to this contact. Free text (the UI offers common
    # values: friend, family, coworker, colleague, client, vendor, acquaintance,
    # mentor, classmate, neighbor, …). Empty / null clears it.
    category: str | None = Field(default=None, max_length=40)


class SetRelationshipCategoryOutput(ToolOutput):
    person_id: uuid.UUID
    relationship_category: str | None


class UpdatePersonInput(BaseModel):
    person_id: uuid.UUID
    etag: str
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    given_name: str | None = Field(default=None, max_length=100)
    family_name: str | None = Field(default=None, max_length=100)
    pronouns: str | None = Field(default=None, max_length=40)
    headline: str | None = Field(default=None, max_length=200)
    bio: str | None = Field(default=None, max_length=4000)
    occupation_title: str | None = Field(default=None, max_length=120)
    source_label: str | None = Field(default=None, max_length=120)
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdatePersonOutput(ToolOutput):
    person_id: uuid.UUID
    etag: str
    changed_fields: list[str]
    status: Literal["applied", "proposed"]
    event_id: uuid.UUID | None = None


class ArchivePersonInput(BaseModel):
    person_id: uuid.UUID
    reason: Literal[
        "left_company", "lost_touch", "duplicate", "requested_removal", "deceased", "spam", "other"
    ]
    reason_note: str | None = Field(default=None, max_length=500)
    archive_in_all_tenants: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)


class ArchivePersonOutput(ToolOutput):
    person_id: uuid.UUID
    status: Literal["applied", "proposed"]
    archived_at: datetime
    event_id: uuid.UUID | None = None
    undo_event_class: str = "person.archive"
    undo_until: None = None


class DeletePersonInput(BaseModel):
    person_id: uuid.UUID
    reason: Literal["gdpr_erasure", "admin_cleanup", "test_data", "legal_hold_released"]
    reason_note: str = Field(min_length=1, max_length=1000)
    confirmation_phrase: Literal["DELETE THIS PERSON PERMANENTLY"]
    retain_anonymized_audit: bool = True


class DeletePersonOutput(ToolOutput):
    person_id: uuid.UUID
    # `deleted_at` is the moment the row was HARD-deleted. There is no tombstone
    # and no future purge job today, so the old `tombstoned_at`/`hard_purge_at`
    # fields were fictions — removed. `undo_until` is None because an immediate
    # hard delete cannot be undone; a real undo window only exists under the
    # (dormant) two-phase compliance engine, which will repopulate it.
    deleted_at: datetime
    event_id: uuid.UUID
    undo_until: datetime | None = None
    status: Literal["applied"]
    cascade_summary: dict[str, int]


class ListPeopleInput(BaseModel):
    tags_any: list[str] | None = Field(default=None, max_length=20)
    tags_all: list[str] | None = Field(default=None, max_length=20)
    current_org_id: uuid.UUID | None = None
    has_email: bool | None = None
    has_email_domain: str | None = Field(default=None, max_length=253)
    relationship_temperature_any: list[Literal["cold", "warm", "hot", "dormant"]] | None = None
    include_archived: bool = False
    sort: Literal["display_name", "last_interaction_at", "created_at", "communication_strength"] = (
        "display_name"
    )
    sort_order: Literal["asc", "desc"] = "asc"
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None


class ListPeopleOutput(ToolOutput):
    items: list[PersonSummary]
    count: int
    next_cursor: str | None = None
    total_count: int | None = None


class SearchPeopleInput(ListPeopleInput):
    query: str | None = Field(default=None, min_length=1, max_length=500)
    query_mode: Literal["lexical", "semantic", "hybrid"] = "hybrid"
    semantic_threshold: float = Field(default=0.55, ge=0, le=1)
    boost_recent_interaction: bool = True


class SearchResult(PersonSummary):
    match_score: float
    match_snippets: list[str] = Field(default_factory=list)
    relationship_category: str | None = None


class SearchPeopleOutput(ToolOutput):
    items: list[SearchResult]
    count: int
    next_cursor: str | None = None
    total_count: int | None = None


class FindPersonByIdentifierInput(BaseModel):
    identifiers: list[IdentifierInput] = Field(min_length=1, max_length=10)
    match_mode: Literal["all_must_match", "any_can_match"] = "any_can_match"


class IdentifierMatch(BaseModel):
    namespace: str
    value: str


class IdentifierMatchOutput(PersonSummary):
    matched_on: list[IdentifierMatch]


class FindPersonByIdentifierOutput(ToolOutput):
    matches: list[IdentifierMatchOutput]
    ambiguous: bool


class BulkUpsertPeopleInput(BaseModel):
    people: list[UpsertPersonInput] = Field(alias="items", min_length=1, max_length=50)
    conflict_strategy: Literal[
        "fill_blanks", "prefer_incoming", "prefer_existing", "propose_only"
    ] = "fill_blanks"
    source_label: str = Field(max_length=120)
    idempotency_key: uuid.UUID | None = None


class BulkResult(BaseModel):
    index: int
    action: str
    person_id: uuid.UUID | None = None
    error_code: str | None = None
    error_message: str | None = None


class BulkUpsertPeopleOutput(ToolOutput):
    created: int
    updated: int
    noop: int
    errors: int
    results: list[BulkResult]
    batch_event_id: uuid.UUID | None = None


class BulkArchivePeopleInput(BaseModel):
    person_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    reason: Literal[
        "left_company", "lost_touch", "duplicate", "requested_removal", "deceased", "spam", "other"
    ]
    reason_note: str | None = Field(default=None, max_length=500)
    confidence: float = Field(default=1.0, ge=0, le=1)
    idempotency_key: uuid.UUID | None = None


class BulkArchivePeopleOutput(ToolOutput):
    archived: int
    already_archived: int
    errors: int
    results: list[BulkResult]
    batch_event_id: uuid.UUID | None = None


def _kind(value: str) -> PersonKind:
    if value == "org":
        return PersonKind.organization_alias
    if value == "location":
        return PersonKind.location_alias
    return PersonKind(value)


def _preferred(value: str | None) -> PreferredContact:
    if value is None:
        return PreferredContact.unknown
    if value == "phone":
        return PreferredContact.call
    if value in {"linkedin", "other"}:
        return PreferredContact.unknown
    return PreferredContact(value)


def _email_type(value: str) -> EmailType:
    return EmailType.personal if value == "home" else EmailType(value)


def _phone_type(value: str) -> PhoneType:
    return PhoneType(value)


async def _insert_person(ctx: MCPContext, data: CreatePersonInput) -> Person:
    etag = str(uuid.uuid4())
    person = Person(
        id=uuid.uuid4(),
        display_name=data.display_name,
        kind=_kind(data.kind),
        given_name=data.given_name,
        family_name=data.family_name,
        additional_names=data.additional_names,
        honorific_prefix=data.honorific_prefix,
        honorific_suffix=data.honorific_suffix,
        nicknames=data.nicknames,
        pronouns=data.pronouns,
        gender_identity=data.gender_identity,
        birthday=data.birthday.model_dump(exclude_none=False) if data.birthday else None,
        preferred_languages=data.preferred_languages,
        time_zone=data.time_zone,
        preferred_contact_channel=_preferred(data.preferred_contact_channel),
        headline=data.headline,
        bio=data.bio,
        occupation_title=data.occupation_title,
        canonical_owner_tenant_id=ctx.tenant_id,
        vcard_uid=f"urn:uuid:{uuid.uuid4()}",
        created_by_actor_id=uuid.UUID(ctx.user_id) if _is_uuid(ctx.user_id) else None,
        etag=etag,
    )
    ctx.db.add(person)
    ctx.db.add(
        PersonTenantMembership(
            person_id=person.id,
            tenant_id=ctx.tenant_id,
            notes=data.tenant_membership_notes,
            tags=data.initial_tags,
            added_by=uuid.UUID(ctx.user_id) if _is_uuid(ctx.user_id) else None,
        )
    )
    for index, email_item in enumerate(data.emails):
        ctx.db.add(
            Email(
                person_id=person.id,
                address=str(email_item.address).lower(),
                type=_email_type(email_item.type),
                is_primary=email_item.is_primary or index == 0,
                confidence=data.confidence,
            )
        )
    for index, phone_item in enumerate(data.phones):
        ctx.db.add(
            Phone(
                person_id=person.id,
                e164=phone_item.e164,
                type=_phone_type(phone_item.type),
                is_primary=phone_item.is_primary or index == 0,
                confidence=data.confidence,
            )
        )
    await ctx.db.flush()
    return person


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError):
        return False
    return True


async def create_person(ctx: MCPContext, args: BaseModel) -> CreatePersonOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write",))
    data = CreatePersonInput.model_validate(args)
    cached = await check_or_register(
        ctx,
        idempotency_key=str(data.idempotency_key) if data.idempotency_key else None,
        tool_name="create_person",
        stable_args_hash=stable_hash(data),
    )
    if cached is not None:
        return CreatePersonOutput.model_validate(cached)
    person = await _insert_person(ctx, data)
    event_id = await emit_action_event(
        ctx,
        event_type="person.create",
        aggregate_type="person",
        aggregate_id=person.id,
        payload_before=None,
        payload_after={"person_id": str(person.id), "display_name": person.display_name},
        confidence=data.confidence,
    )
    output = CreatePersonOutput(
        person_id=person.id,
        etag=person.etag,
        vcard_uid=person.vcard_uid or "",
        display_name=person.display_name,
        kind=person.kind.value,
        created_at=person.created_at,
        status="applied",
        event_id=event_id,
    )
    if data.idempotency_key:
        await store_result(ctx, str(data.idempotency_key), output.model_dump(mode="json"))
    return output


async def _find_by_identifier(
    ctx: MCPContext, identifiers: list[IdentifierInput]
) -> list[tuple[Person, list[IdentifierMatch]]]:
    found: dict[uuid.UUID, tuple[Person, list[IdentifierMatch]]] = {}
    for ident in identifiers:
        namespace = ident.namespace.lower()
        value = ident.value.strip()
        rows: list[tuple[Person, str]]
        if namespace == "email":
            result = await ctx.db.execute(
                select(Person, Email.address)
                .join(Email, Email.person_id == Person.id)
                .where(
                    Person.canonical_owner_tenant_id == ctx.tenant_id,
                    func.lower(Email.address) == value.lower(),
                )
            )
            rows = [(row[0], row[1]) for row in result.all()]
        elif namespace == "phone":
            result = await ctx.db.execute(
                select(Person, Phone.e164)
                .join(Phone, Phone.person_id == Person.id)
                .where(Person.canonical_owner_tenant_id == ctx.tenant_id, Phone.e164 == value)
            )
            rows = [(row[0], row[1]) for row in result.all()]
        else:
            rows = []
        for person, matched_value in rows:
            matched = IdentifierMatch(namespace=namespace, value=matched_value)
            if person.id in found:
                found[person.id][1].append(matched)
            else:
                found[person.id] = (person, [matched])
    return list(found.values())


async def upsert_person(ctx: MCPContext, args: BaseModel) -> UpsertPersonOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write",))
    data = UpsertPersonInput.model_validate(args)
    cached = await check_or_register(
        ctx,
        idempotency_key=str(data.idempotency_key) if data.idempotency_key else None,
        tool_name="upsert_person",
        stable_args_hash=stable_hash(data),
    )
    if cached is not None:
        return UpsertPersonOutput.model_validate(cached)
    matches = await _find_by_identifier(ctx, data.match_by)
    if len(matches) > 1:
        raise ToolError(AMBIGUOUS_MATCH, "multiple natural-key matches found", retryable=False)
    if not matches:
        if data.display_name is None:
            raise ToolError(VALIDATION_FAILED, "display_name is required when creating")
        person = await _insert_person(ctx, data)
        event_type = "person.upsert.create"
        action: Literal["created", "updated", "noop"] = "created"
        changed_fields = ["*"]
    else:
        person = matches[0][0]
        changed_fields = []
        for field in (
            "display_name",
            "given_name",
            "family_name",
            "pronouns",
            "headline",
            "bio",
            "occupation_title",
        ):
            incoming = getattr(data, field)
            if incoming is None:
                continue
            current = getattr(person, field)
            if data.conflict_strategy == "prefer_existing" and current:
                continue
            if current != incoming:
                setattr(person, field, incoming)
                changed_fields.append(field)
        if changed_fields:
            person.etag = str(uuid.uuid4())
        action = "updated" if changed_fields else "noop"
        event_type = "person.upsert.update"
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type=event_type,
        aggregate_type="person",
        aggregate_id=person.id,
        payload_before=None,
        payload_after={"action": action, "changed_fields": changed_fields},
        confidence=data.confidence,
    )
    output = UpsertPersonOutput(
        person_id=person.id,
        action=action,
        changed_fields=changed_fields,
        etag=person.etag,
        status="applied",
        event_id=event_id,
    )
    if data.idempotency_key:
        await store_result(ctx, str(data.idempotency_key), output.model_dump(mode="json"))
    return output


async def get_person(ctx: MCPContext, args: BaseModel) -> GetPersonOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("person:read",))
    data = GetPersonInput.model_validate(args)
    person = await load_person(ctx, data.person_id)
    emails = None
    phones = None
    if "emails" in data.include:
        result = await ctx.db.execute(
            select(Email).where(Email.person_id == person.id).order_by(Email.is_primary.desc())
        )
        emails = [
            {
                "id": str(row.id),
                "address": row.address,
                "type": row.type.value,
                "is_primary": row.is_primary,
            }
            for row in result.scalars()
        ]
    if "phones" in data.include:
        result = await ctx.db.execute(
            select(Phone).where(Phone.person_id == person.id).order_by(Phone.is_primary.desc())
        )
        phone_rows = cast(list[Phone], result.scalars().all())
        phones = [
            {
                "id": str(row.id),
                "e164": row.e164,
                "type": row.type.value,
                "is_primary": row.is_primary,
            }
            for row in phone_rows
        ]
    membership = await ctx.db.get(PersonTenantMembership, (person.id, ctx.tenant_id))
    relationship_category = (
        (membership.custom_attrs or {}).get("relationship_category") if membership else None
    )
    events = await ctx.db.execute(
        select(ActionEvent)
        .where(ActionEvent.tenant_id == ctx.tenant_id, ActionEvent.aggregate_id == person.id)
        .order_by(ActionEvent.proposed_at.desc())
        .limit(10)
    )
    return GetPersonOutput(
        person_id=person.id,
        etag=person.etag,
        vcard_uid=person.vcard_uid,
        display_name=person.display_name,
        kind=person.kind.value,
        status="archived" if person.merge_status == MergeStatus.archived else "active",
        merged_into_id=person.merged_into_id,
        emails=emails,
        phones=phones,
        relationship_category=relationship_category,
        recent_events=[
            {
                "event_id": str(event.event_id),
                "event_type": event.event_type,
                "status": event.status.value,
            }
            for event in events.scalars()
        ],
    )


async def set_relationship_category(
    ctx: MCPContext, args: BaseModel
) -> SetRelationshipCategoryOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write",))
    data = SetRelationshipCategoryInput.model_validate(args)
    person = await load_person(ctx, data.person_id)
    category = (data.category or "").strip() or None

    membership = await ctx.db.get(PersonTenantMembership, (person.id, ctx.tenant_id))
    if membership is None:
        membership = PersonTenantMembership(person_id=person.id, tenant_id=ctx.tenant_id)
        ctx.db.add(membership)
    # Reassign a NEW dict so SQLAlchemy flags the JSONB column dirty (in-place
    # mutation of a JSONB dict isn't tracked).
    attrs = dict(membership.custom_attrs or {})
    if category:
        attrs["relationship_category"] = category
    else:
        attrs.pop("relationship_category", None)
    membership.custom_attrs = attrs
    await ctx.db.flush()
    return SetRelationshipCategoryOutput(
        person_id=person.id, relationship_category=category
    )


async def update_person(ctx: MCPContext, args: BaseModel) -> UpdatePersonOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write",))
    data = UpdatePersonInput.model_validate(args)
    person = await load_person(ctx, data.person_id)
    if person.etag != data.etag:
        raise ToolError(STALE_ETAG, "etag does not match current person version", retryable=False)
    before = {"etag": person.etag, "display_name": person.display_name}
    changed = []
    supplied = data.model_fields_set
    for field in (
        "display_name",
        "given_name",
        "family_name",
        "pronouns",
        "headline",
        "bio",
        "occupation_title",
    ):
        if field in supplied and getattr(person, field) != getattr(data, field):
            setattr(person, field, getattr(data, field))
            changed.append(field)
    if changed:
        person.etag = str(uuid.uuid4())
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="person.update",
        aggregate_type="person",
        aggregate_id=person.id,
        payload_before=before,
        payload_after={"changed_fields": changed, "etag": person.etag},
        confidence=data.confidence,
        evidence={"source_label": data.source_label, "request_id": ctx.request_id},
    )
    return UpdatePersonOutput(
        person_id=person.id,
        etag=person.etag,
        changed_fields=changed,
        status="applied",
        event_id=event_id,
    )


async def archive_person(ctx: MCPContext, args: BaseModel) -> ArchivePersonOutput:
    require_role(ctx, "MANAGER")
    require_scopes(ctx, ("person:archive",))
    data = ArchivePersonInput.model_validate(args)
    person = await load_person(ctx, data.person_id)
    archived_at = now_utc()
    person.merge_status = MergeStatus.archived
    person.etag = str(uuid.uuid4())
    await ctx.db.execute(
        sa.update(PersonTenantMembership)
        .where(
            PersonTenantMembership.person_id == person.id,
            PersonTenantMembership.tenant_id == ctx.tenant_id,
        )
        .values(visibility="archived")
    )
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="person.archive",
        aggregate_type="person",
        aggregate_id=person.id,
        payload_before=None,
        payload_after={"reason": data.reason, "reason_note": data.reason_note},
        confidence=data.confidence,
    )
    return ArchivePersonOutput(
        person_id=person.id, status="applied", archived_at=archived_at, event_id=event_id
    )


async def delete_person(ctx: MCPContext, args: BaseModel) -> DeletePersonOutput:
    require_role(ctx, "ADMIN")
    require_scopes(ctx, ("person:delete",))
    data = DeletePersonInput.model_validate(args)
    if data.confirmation_phrase != "DELETE THIS PERSON PERMANENTLY":
        raise ToolError(CONFIRMATION_PHRASE_MISMATCH, "confirmation phrase mismatch")
    person = await load_person(ctx, data.person_id)
    if person.legal_hold:
        raise ToolError(DEPENDENCY_VIOLATION, "person is under legal hold")
    if person.merge_status != MergeStatus.archived:
        raise ToolError(DEPENDENCY_VIOLATION, "delete_person requires an archived person first")
    deleted_at = now_utc()
    # Count what we actually remove so cascade_summary reports the TRUTH
    # (it was hardcoded to zeros). rowcount is reliable for these DELETEs.
    emails_res = await ctx.db.execute(sa.delete(Email).where(Email.person_id == person.id))
    phones_res = await ctx.db.execute(sa.delete(Phone).where(Phone.person_id == person.id))
    memberships_res = await ctx.db.execute(
        sa.delete(PersonTenantMembership).where(PersonTenantMembership.person_id == person.id)
    )
    # dedup_pair_candidates.person_a_id / person_b_id are FKs to persons.id with
    # NO ondelete (RESTRICT) — a pending dedup candidate makes the person delete
    # ERROR. Clear them first, or this tool throws on any contact that has one.
    candidates_res = await ctx.db.execute(
        sa.delete(MergeCandidate).where(
            sa.or_(
                MergeCandidate.person_a_id == person.id,
                MergeCandidate.person_b_id == person.id,
            )
        )
    )
    # NOTE: ondelete=CASCADE tables (addresses, identifiers, im_handles, urls,
    # photos, person_org_role, relations, aliases, voice_consent/fingerprint) are
    # cleaned by the DB on the person delete below. The no-FK orphan tables
    # (facts.subject_id, notes.target_id, merge_history, field_provenance) and the
    # external stores (Garage bytes, Qdrant vectors, FalkorDB nodes) are NOT
    # purged here — that full Art.17 erasure lands with the dormant compliance
    # engine (services/compliance_erasure.py). This tool stays honest about what
    # it does: the cascade_summary below reflects ONLY the rows it removed.
    await ctx.db.delete(person)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="person.delete",
        aggregate_type="person",
        aggregate_id=data.person_id,
        payload_before={"person_id": str(data.person_id)},
        payload_after={
            "reason": data.reason,
            "retain_anonymized_audit": data.retain_anonymized_audit,
        },
        status="applied",
    )
    return DeletePersonOutput(
        person_id=data.person_id,
        deleted_at=deleted_at,
        event_id=event_id,
        undo_until=None,  # immediate hard delete — there is no undo window
        status="applied",
        cascade_summary={
            "emails": emails_res.rowcount or 0,
            "phones": phones_res.rowcount or 0,
            "memberships": memberships_res.rowcount or 0,
            "merge_candidates": candidates_res.rowcount or 0,
        },
    )


def _apply_list_filters(
    stmt: Select[tuple[Person]], ctx: MCPContext, data: ListPeopleInput
) -> Select[tuple[Person]]:
    stmt = stmt.where(Person.canonical_owner_tenant_id == ctx.tenant_id)
    if not data.include_archived:
        stmt = stmt.where(Person.merge_status != MergeStatus.archived)
    if data.current_org_id:
        stmt = stmt.where(Person.current_org_id == data.current_org_id)
    if data.relationship_temperature_any:
        stmt = stmt.where(
            Person.relationship_temperature.in_(
                [TemperatureState(v) for v in data.relationship_temperature_any]
            )
        )
    if data.has_email is True:
        stmt = stmt.where(sa.exists(select(Email.id).where(Email.person_id == Person.id)))
    if data.has_email is False:
        stmt = stmt.where(~sa.exists(select(Email.id).where(Email.person_id == Person.id)))
    if data.has_email_domain:
        stmt = stmt.where(
            sa.exists(
                select(Email.id).where(
                    Email.person_id == Person.id, Email.address.ilike(f"%@{data.has_email_domain}")
                )
            )
        )
    return stmt


async def list_people(ctx: MCPContext, args: BaseModel) -> ListPeopleOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("person:read",))
    data = ListPeopleInput.model_validate(args)
    if not any(
        [
            data.tags_any,
            data.tags_all,
            data.current_org_id,
            data.has_email is not None,
            data.has_email_domain,
            data.relationship_temperature_any,
        ]
    ):
        raise ToolError(
            NO_FILTER_PROVIDED,
            "list_people requires at least one narrowing filter",
            hint="use search_people for free-text",
        )
    stmt = _apply_list_filters(select(Person), ctx, data)
    order_col = Person.display_name if data.sort == "display_name" else Person.created_at
    stmt = stmt.order_by(order_col.desc() if data.sort_order == "desc" else order_col.asc()).limit(
        data.limit
    )
    result = await ctx.db.execute(stmt)
    people = result.scalars().all()
    items = [person_summary(person, await primary_email_for(ctx, person.id)) for person in people]
    return ListPeopleOutput(items=items, count=len(items), next_cursor=None)


async def search_people(ctx: MCPContext, args: BaseModel) -> SearchPeopleOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("person:read",))
    data = SearchPeopleInput.model_validate(args)
    stmt = _apply_list_filters(select(Person), ctx, data)
    if data.query:
        q = f"%{data.query}%"
        stmt = stmt.where(
            or_(
                Person.display_name.ilike(q),
                Person.given_name.ilike(q),
                Person.family_name.ilike(q),
                Person.bio.ilike(q),
                Person.headline.ilike(q),
            )
        )
    total = await ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    # Offset pagination via the cursor (a stringified offset). search_people was
    # previously single-page (next_cursor always None), so the directory could
    # only ever show one page; this lets the UI walk the whole tenant A→Z.
    offset = 0
    if data.cursor:
        try:
            offset = max(0, int(data.cursor))
        except (TypeError, ValueError):
            offset = 0
    stmt = stmt.order_by(Person.display_name.asc()).offset(offset).limit(data.limit)
    result = await ctx.db.execute(stmt)
    rows = result.scalars().all()
    # Batch-load this page's relationship categories (the owner's per-tenant view)
    # in one query, so the directory can show + edit them without an N+1.
    cat_map: dict[uuid.UUID, str | None] = {}
    if rows:
        membership_rows = await ctx.db.execute(
            select(PersonTenantMembership.person_id, PersonTenantMembership.custom_attrs).where(
                PersonTenantMembership.tenant_id == ctx.tenant_id,
                PersonTenantMembership.person_id.in_([p.id for p in rows]),
            )
        )
        cat_map = {pid: (attrs or {}).get("relationship_category") for pid, attrs in membership_rows}
    items = [
        SearchResult(
            **person_summary(person, await primary_email_for(ctx, person.id)).model_dump(),
            match_score=1.0,
            match_snippets=[],
            relationship_category=cat_map.get(person.id),
        )
        for person in rows
    ]
    next_offset = offset + len(rows)
    next_cursor = str(next_offset) if total and next_offset < int(total) else None
    return SearchPeopleOutput(
        items=items, count=len(items), total_count=int(total or 0), next_cursor=next_cursor
    )


async def find_person_by_identifier(
    ctx: MCPContext, args: BaseModel
) -> FindPersonByIdentifierOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("person:read",))
    data = FindPersonByIdentifierInput.model_validate(args)
    matches = await _find_by_identifier(ctx, data.identifiers)
    if data.match_mode == "all_must_match":
        needed = {(i.namespace.lower(), i.value.lower()) for i in data.identifiers}
        matches = [
            (person, found)
            for person, found in matches
            if needed <= {(m.namespace, m.value.lower()) for m in found}
        ]
    if len(matches) > 1:
        raise ToolError(
            AMBIGUOUS_MATCH,
            "identifier matched multiple people",
            retryable=False,
            hint="use suggest_merge to resolve",
        )
    items = [
        IdentifierMatchOutput(
            **person_summary(person, await primary_email_for(ctx, person.id)).model_dump(),
            matched_on=found,
        )
        for person, found in matches
    ]
    return FindPersonByIdentifierOutput(matches=items, ambiguous=False)


async def bulk_upsert_people(ctx: MCPContext, args: BaseModel) -> BulkUpsertPeopleOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write", "person:bulk"))
    data = BulkUpsertPeopleInput.model_validate(args)
    cached = await check_or_register(
        ctx,
        idempotency_key=str(data.idempotency_key) if data.idempotency_key else None,
        tool_name="bulk_upsert_people",
        stable_args_hash=stable_hash(data),
    )
    if cached is not None:
        return BulkUpsertPeopleOutput.model_validate(cached)
    results: list[BulkResult] = []
    created = updated = noop = errors = 0
    for index, item in enumerate(data.people):
        try:
            item_output = await upsert_person(ctx, item)
            created += int(item_output.action == "created")
            updated += int(item_output.action == "updated")
            noop += int(item_output.action == "noop")
            results.append(
                BulkResult(index=index, action=item_output.action, person_id=item_output.person_id)
            )
        except ToolError as exc:
            errors += 1
            results.append(
                BulkResult(
                    index=index, action="error", error_code=exc.code, error_message=exc.message
                )
            )
    batch_event_id = await emit_action_event(
        ctx,
        event_type="person.bulk_upsert",
        aggregate_type="person",
        aggregate_id=uuid.uuid4(),
        payload_before=None,
        payload_after={"count": len(results)},
    )
    batch_output = BulkUpsertPeopleOutput(
        created=created,
        updated=updated,
        noop=noop,
        errors=errors,
        results=results,
        batch_event_id=batch_event_id,
    )
    if data.idempotency_key:
        await store_result(ctx, str(data.idempotency_key), batch_output.model_dump(mode="json"))
    return batch_output


async def bulk_archive_people(ctx: MCPContext, args: BaseModel) -> BulkArchivePeopleOutput:
    require_role(ctx, "MANAGER")
    require_scopes(ctx, ("person:archive", "person:bulk"))
    data = BulkArchivePeopleInput.model_validate(args)
    results: list[BulkResult] = []
    archived = already = errors = 0
    for index, person_id in enumerate(data.person_ids):
        try:
            person = await load_person(ctx, person_id)
            if person.merge_status == MergeStatus.archived:
                already += 1
                results.append(
                    BulkResult(index=index, action="already_archived", person_id=person_id)
                )
                continue
            output = await archive_person(
                ctx,
                ArchivePersonInput(
                    person_id=person_id,
                    reason=data.reason,
                    reason_note=data.reason_note,
                    confidence=data.confidence,
                ),
            )
            archived += 1
            results.append(BulkResult(index=index, action="archived", person_id=output.person_id))
        except ToolError as exc:
            errors += 1
            results.append(
                BulkResult(index=index, action="error", person_id=person_id, error_code=exc.code)
            )
    batch_event_id = await emit_action_event(
        ctx,
        event_type="person.bulk_archive",
        aggregate_type="person",
        aggregate_id=uuid.uuid4(),
        payload_before=None,
        payload_after={"count": len(results)},
    )
    return BulkArchivePeopleOutput(
        archived=archived,
        already_archived=already,
        errors=errors,
        results=results,
        batch_event_id=batch_event_id,
    )


def register_people_tools() -> None:
    base = {"destructiveHint": False, "openWorldHint": False}
    register(
        name="create_person",
        description="Create a brand-new person in the caller tenant. Use only when the person is known to be new; STAFF with person:write required.",
        input_model=CreatePersonInput,
        output_model=CreatePersonOutput,
        handler=create_person,
        required_role="STAFF",
        required_scopes=("person:write",),
        annotations={**base, "readOnlyHint": False, "idempotentHint": False},
        idempotency="idempotency-key",
    )
    register(
        name="upsert_person",
        description="Create or patch a person by natural identifier. Use for ingestion and uncertain existence checks; STAFF with person:write required.",
        input_model=UpsertPersonInput,
        output_model=UpsertPersonOutput,
        handler=upsert_person,
        required_role="STAFF",
        required_scopes=("person:write",),
        annotations={**base, "readOnlyHint": False, "idempotentHint": True},
        idempotency="natural-key",
    )
    register(
        name="get_person",
        description="Fetch one visible person by system ID with optional expansions. Read-only; CLIENT with person:read required.",
        input_model=GetPersonInput,
        output_model=GetPersonOutput,
        handler=get_person,
        required_role="CLIENT",
        required_scopes=("person:read",),
        annotations={**base, "readOnlyHint": True, "idempotentHint": True},
        idempotency="none",
    )
    register(
        name="update_person",
        description="Patch editable person fields with etag concurrency. Use for single-record edits; STAFF with person:write required.",
        input_model=UpdatePersonInput,
        output_model=UpdatePersonOutput,
        handler=update_person,
        required_role="STAFF",
        required_scopes=("person:write",),
        annotations={**base, "readOnlyHint": False, "idempotentHint": True},
        idempotency="none",
    )
    register(
        name="set_relationship_category",
        description=(
            "Set the OWNER's relationship to a contact (friend, coworker, family, "
            "client, …) — a per-tenant personal label, not a shared fact. Empty "
            "category clears it. STAFF with person:write required."
        ),
        input_model=SetRelationshipCategoryInput,
        output_model=SetRelationshipCategoryOutput,
        handler=set_relationship_category,
        required_role="STAFF",
        required_scopes=("person:write",),
        annotations={**base, "readOnlyHint": False, "idempotentHint": True},
        idempotency="none",
    )
    register(
        name="archive_person",
        description="Soft-archive a person while retaining audit and relationship history. MANAGER with person:archive required.",
        input_model=ArchivePersonInput,
        output_model=ArchivePersonOutput,
        handler=archive_person,
        required_role="MANAGER",
        required_scopes=("person:archive",),
        annotations={**base, "readOnlyHint": False, "idempotentHint": True},
        idempotency="natural-key",
    )
    register(
        name="delete_person",
        description="Glass-break hard-delete for archived people only. Requires ADMIN, person:delete, and exact confirmation phrase.",
        input_model=DeletePersonInput,
        output_model=DeletePersonOutput,
        handler=delete_person,
        required_role="ADMIN",
        required_scopes=("person:delete",),
        annotations={
            **base,
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
        idempotency="natural-key",
    )
    register(
        name="list_people",
        description="Filtered person listing for known narrow criteria. Refuses unfiltered tenant dumps; CLIENT with person:read required.",
        input_model=ListPeopleInput,
        output_model=ListPeopleOutput,
        handler=list_people,
        required_role="CLIENT",
        required_scopes=("person:read",),
        annotations={**base, "readOnlyHint": True, "idempotentHint": True},
        idempotency="none",
    )
    register(
        name="search_people",
        description="Free-text and structured person search. Uses lexical matching in Phase 1 with vector hook preserved; CLIENT with person:read required.",
        input_model=SearchPeopleInput,
        output_model=SearchPeopleOutput,
        handler=search_people,
        required_role="CLIENT",
        required_scopes=("person:read",),
        annotations={**base, "readOnlyHint": True, "idempotentHint": True},
        idempotency="none",
    )
    register(
        name="find_person_by_identifier",
        description="Exact lookup by email, phone, or future namespaced identifiers. Returns AMBIGUOUS_MATCH when multiple people match.",
        input_model=FindPersonByIdentifierInput,
        output_model=FindPersonByIdentifierOutput,
        handler=find_person_by_identifier,
        required_role="CLIENT",
        required_scopes=("person:read",),
        annotations={**base, "readOnlyHint": True, "idempotentHint": True},
        idempotency="none",
    )
    register(
        name="bulk_upsert_people",
        description="Upsert up to 50 people with per-item outcomes and a batch audit event. STAFF with person:write and person:bulk required.",
        input_model=BulkUpsertPeopleInput,
        output_model=BulkUpsertPeopleOutput,
        handler=bulk_upsert_people,
        required_role="STAFF",
        required_scopes=("person:write", "person:bulk"),
        annotations={**base, "readOnlyHint": False, "idempotentHint": True},
        idempotency="idempotency-key",
    )
    register(
        name="bulk_archive_people",
        description="Archive up to 50 people with per-item outcomes and a batch audit event. MANAGER with archive and bulk scopes required.",
        input_model=BulkArchivePeopleInput,
        output_model=BulkArchivePeopleOutput,
        handler=bulk_archive_people,
        required_role="MANAGER",
        required_scopes=("person:archive", "person:bulk"),
        annotations={**base, "readOnlyHint": False, "idempotentHint": True},
        idempotency="idempotency-key",
    )
