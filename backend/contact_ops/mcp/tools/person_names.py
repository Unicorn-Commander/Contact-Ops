from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import DUPLICATE_RECORD, VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, now_utc, register
from contact_ops.models import Person, PersonName
from contact_ops.models.enums import NameType

NameTypeLiteral = Literal["legal", "preferred", "nickname", "aka", "former", "maiden", "stage"]


class AddPersonNameInput(BaseModel):
    person_id: uuid.UUID
    name_type: NameTypeLiteral
    value: str = Field(min_length=1, max_length=400)
    given_name: str | None = Field(default=None, max_length=200)
    family_name: str | None = Field(default=None, max_length=200)
    source_id: uuid.UUID | None = None
    is_primary: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)


class PersonNameOutput(ToolOutput):
    person_name_id: uuid.UUID
    person_id: uuid.UUID
    name_type: str
    value: str
    is_primary: bool
    status: str = "applied"
    event_id: uuid.UUID | None = None


class PersonNameIdInput(BaseModel):
    person_name_id: uuid.UUID
    confidence: float = Field(default=1.0, ge=0, le=1)


class RemovePersonNameInput(PersonNameIdInput):
    reason: str | None = Field(default=None, max_length=400)


class RemovePersonNameOutput(ToolOutput):
    person_name_id: uuid.UUID
    removed_at: datetime
    status: str
    event_id: uuid.UUID | None = None


class UpdatePersonNameInput(BaseModel):
    person_name_id: uuid.UUID
    value: str | None = Field(default=None, min_length=1, max_length=400)
    given_name: str | None = Field(default=None, max_length=200)
    family_name: str | None = Field(default=None, max_length=200)
    is_primary: bool | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdatePersonNameOutput(ToolOutput):
    person_name_id: uuid.UUID
    changed_fields: list[str]
    status: str
    event_id: uuid.UUID | None = None


class ListPersonNamesInput(BaseModel):
    person_id: uuid.UUID
    name_type: NameTypeLiteral | None = None


class ListPersonNamesOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int


def _payload(row: PersonName) -> dict[str, Any]:
    return {
        "person_name_id": str(row.id),
        "person_id": str(row.person_id),
        "name_type": row.name_type.value,
        "value": row.value,
        "given_name": row.given_name,
        "family_name": row.family_name,
        "is_primary": row.is_primary,
    }


async def _load_name(ctx: MCPContext, person_name_id: uuid.UUID) -> PersonName:
    row = await ctx.db.get(PersonName, person_name_id)
    if row is None:
        raise ToolError(VALIDATION_FAILED, "person name not found")
    person = await load_person(ctx, row.person_id)
    await ctx.db.refresh(person, with_for_update=True)
    await ctx.db.refresh(row)
    return row


async def _demote(ctx: MCPContext, person_id: uuid.UUID, name_type: NameType) -> None:
    rows = (
        await ctx.db.execute(
            select(PersonName).where(
                PersonName.person_id == person_id,
                PersonName.name_type == name_type,
                PersonName.is_primary.is_(True),
            )
        )
    ).scalars()
    for row in rows:
        row.is_primary = False


async def _append_nickname(ctx: MCPContext, person_id: uuid.UUID, value: str) -> None:
    """Append a nickname to persons.nicknames (idempotent)."""
    person = await ctx.db.get(Person, person_id)
    if person is not None:
        nicknames = list(person.nicknames or [])
        if value not in nicknames:
            nicknames.append(value)
            person.nicknames = nicknames
    await ctx.db.flush()


async def _remove_nickname(ctx: MCPContext, person_id: uuid.UUID, value: str) -> None:
    """Remove a nickname from persons.nicknames (idempotent)."""
    person = await ctx.db.get(Person, person_id)
    if person is not None:
        person.nicknames = [n for n in (person.nicknames or []) if n != value]
    await ctx.db.flush()


async def _emit(
    ctx: MCPContext,
    *,
    event_type: str,
    person_id: uuid.UUID,
    payload_before: dict[str, Any] | None,
    payload_after: dict[str, Any],
    confidence: float,
) -> uuid.UUID:
    # person_names has no dedicated entity_kind; aggregate on the person so the
    # audit ledger stays enum-valid without a schema change.
    return await emit_action_event(
        ctx,
        event_type=event_type,
        aggregate_type="person",
        aggregate_id=person_id,
        affected_ids=[person_id],
        payload_before=payload_before,
        payload_after=payload_after,
        confidence=confidence,
    )


async def add_person_name(ctx: MCPContext, req: AddPersonNameInput) -> PersonNameOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["name:write"])
    person = await load_person(ctx, req.person_id)
    await ctx.db.refresh(person, with_for_update=True)
    value = req.value.strip()
    if not value:
        raise ToolError(VALIDATION_FAILED, "name value is required")
    name_type = NameType(req.name_type)
    existing = await ctx.db.scalar(
        select(PersonName).where(
            PersonName.person_id == req.person_id,
            PersonName.name_type == name_type,
            func.lower(PersonName.value) == value.lower(),
        )
    )
    if existing:
        return PersonNameOutput(
            person_name_id=existing.id,
            person_id=req.person_id,
            name_type=existing.name_type.value,
            value=existing.value,
            is_primary=existing.is_primary,
        )
    is_primary = req.is_primary or not await ctx.db.scalar(
        select(PersonName.id)
        .where(PersonName.person_id == req.person_id, PersonName.name_type == name_type)
        .limit(1)
    )
    if is_primary:
        await _demote(ctx, req.person_id, name_type)
    row = PersonName(
        person_id=req.person_id,
        name_type=name_type,
        value=value,
        given_name=req.given_name,
        family_name=req.family_name,
        source_id=req.source_id,
        is_primary=is_primary,
        confidence=Decimal(str(req.confidence)),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    if name_type == NameType.nickname:
        await _append_nickname(ctx, req.person_id, value)
    event_id = await _emit(
        ctx,
        event_type="person_name.add",
        person_id=req.person_id,
        payload_before=None,
        payload_after=_payload(row),
        confidence=req.confidence,
    )
    return PersonNameOutput(
        person_name_id=row.id,
        person_id=req.person_id,
        name_type=row.name_type.value,
        value=row.value,
        is_primary=row.is_primary,
        event_id=event_id,
    )


async def remove_person_name(ctx: MCPContext, req: RemovePersonNameInput) -> RemovePersonNameOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["name:write"])
    row = await _load_name(ctx, req.person_name_id)
    person_id = row.person_id
    was_nickname = row.name_type == NameType.nickname
    removed_value = row.value if was_nickname else None
    before = _payload(row)
    await ctx.db.delete(row)
    await ctx.db.flush()
    if was_nickname and removed_value is not None:
        await _remove_nickname(ctx, person_id, removed_value)
    removed_at = now_utc()
    event_id = await _emit(
        ctx,
        event_type="person_name.remove",
        person_id=person_id,
        payload_before=before,
        payload_after={"removed_at": removed_at.isoformat(), "reason": req.reason},
        confidence=req.confidence,
    )
    return RemovePersonNameOutput(
        person_name_id=req.person_name_id,
        removed_at=removed_at,
        status="applied",
        event_id=event_id,
    )


async def update_person_name(ctx: MCPContext, req: UpdatePersonNameInput) -> UpdatePersonNameOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["name:write"])
    row = await _load_name(ctx, req.person_name_id)
    before = _payload(row)
    changed: list[str] = []
    old_value = row.value
    if req.value is not None:
        value = req.value.strip()
        if not value:
            raise ToolError(VALIDATION_FAILED, "name value is required")
        if row.value != value:
            duplicate = await ctx.db.scalar(
                select(PersonName.id).where(
                    PersonName.person_id == row.person_id,
                    PersonName.name_type == row.name_type,
                    func.lower(PersonName.value) == value.lower(),
                    PersonName.id != row.id,
                )
            )
            if duplicate:
                raise ToolError(DUPLICATE_RECORD, "name already exists")
            row.value = value
            changed.append("value")
    if req.given_name is not None and row.given_name != req.given_name:
        row.given_name = req.given_name
        changed.append("given_name")
    if req.family_name is not None and row.family_name != req.family_name:
        row.family_name = req.family_name
        changed.append("family_name")
    if req.is_primary is not None and row.is_primary != req.is_primary:
        row.is_primary = req.is_primary
        changed.append("is_primary")
        if req.is_primary:
            await _demote(ctx, row.person_id, row.name_type)
            row.is_primary = True
    await ctx.db.flush()
    if row.name_type == NameType.nickname and "value" in changed:
        await _remove_nickname(ctx, row.person_id, old_value)
        await _append_nickname(ctx, row.person_id, row.value)
    event_id = None
    if changed:
        event_id = await _emit(
            ctx,
            event_type="person_name.update",
            person_id=row.person_id,
            payload_before=before,
            payload_after=_payload(row),
            confidence=req.confidence,
        )
    return UpdatePersonNameOutput(
        person_name_id=row.id,
        changed_fields=changed,
        status="applied",
        event_id=event_id,
    )


async def set_primary_person_name(ctx: MCPContext, req: PersonNameIdInput) -> PersonNameOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["name:write"])
    row = await _load_name(ctx, req.person_name_id)
    before = _payload(row)
    await _demote(ctx, row.person_id, row.name_type)
    row.is_primary = True
    await ctx.db.flush()
    event_id = await _emit(
        ctx,
        event_type="person_name.primary_set",
        person_id=row.person_id,
        payload_before=before,
        payload_after=_payload(row),
        confidence=req.confidence,
    )
    return PersonNameOutput(
        person_name_id=row.id,
        person_id=row.person_id,
        name_type=row.name_type.value,
        value=row.value,
        is_primary=True,
        event_id=event_id,
    )


async def list_person_names(ctx: MCPContext, req: ListPersonNamesInput) -> ListPersonNamesOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["name:read"])
    await load_person(ctx, req.person_id)
    stmt = select(PersonName).where(PersonName.person_id == req.person_id)
    if req.name_type is not None:
        stmt = stmt.where(PersonName.name_type == NameType(req.name_type))
    rows = (
        (await ctx.db.execute(stmt.order_by(PersonName.name_type, PersonName.created_at)))
        .scalars()
        .all()
    )
    return ListPersonNamesOutput(
        items=[
            {
                "person_name_id": str(row.id),
                "name_type": row.name_type.value,
                "value": row.value,
                "given_name": row.given_name,
                "family_name": row.family_name,
                "is_primary": row.is_primary,
                "confidence": float(row.confidence),
            }
            for row in rows
        ],
        count=len(rows),
    )


for _name, _input, _output, _handler, _role, _scopes, _readonly in [
    (
        "add_person_name",
        AddPersonNameInput,
        PersonNameOutput,
        add_person_name,
        "STAFF",
        ("name:write",),
        False,
    ),
    (
        "remove_person_name",
        RemovePersonNameInput,
        RemovePersonNameOutput,
        remove_person_name,
        "STAFF",
        ("name:write",),
        False,
    ),
    (
        "update_person_name",
        UpdatePersonNameInput,
        UpdatePersonNameOutput,
        update_person_name,
        "STAFF",
        ("name:write",),
        False,
    ),
    (
        "set_primary_person_name",
        PersonNameIdInput,
        PersonNameOutput,
        set_primary_person_name,
        "STAFF",
        ("name:write",),
        False,
    ),
    (
        "list_person_names",
        ListPersonNamesInput,
        ListPersonNamesOutput,
        list_person_names,
        "CLIENT",
        ("name:read",),
        True,
    ),
]:
    register(
        name=_name,
        description=(
            f"{_name.replace('_', ' ')} for typed person names (legal/preferred/nickname/"
            "aka/former/maiden/stage) with tenant RBAC, nickname write-sync, and audit."
        ),
        input_model=cast(type[BaseModel], _input),
        output_model=cast(type[BaseModel], _output),
        handler=_handler,
        required_role=_role,
        required_scopes=_scopes,
        annotations={
            "readOnlyHint": _readonly,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key" if _name == "add_person_name" else "none",
    )
