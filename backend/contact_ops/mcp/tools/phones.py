from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat
from pydantic import BaseModel, Field
from sqlalchemy import select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import DUPLICATE_RECORD, VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, now_utc, register
from contact_ops.models import Phone
from contact_ops.models.enums import LineType, PhoneType


class NormalizePhoneInput(BaseModel):
    raw: str = Field(min_length=3, max_length=50)
    default_country: str = Field(default="US", pattern=r"^[A-Z]{2}$")


class NormalizePhoneOutput(ToolOutput):
    e164: str | None = None
    national_number: str | None = None
    country_code: int | None = None
    type_guess: str | None = None
    valid: bool
    error: str | None = None


class AddPhoneInput(BaseModel):
    person_id: uuid.UUID
    raw: str | None = Field(default=None, min_length=3, max_length=50)
    e164: str | None = Field(default=None, pattern=r"^\+[1-9]\d{1,14}$")
    default_country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    extension: str | None = Field(default=None, max_length=10)
    type: Literal["mobile", "home", "work", "fax", "main", "other"] = "mobile"
    label: str | None = Field(default=None, max_length=40)
    is_primary: bool = False
    is_sms_capable: bool = False
    is_whatsapp: bool = False
    is_signal: bool = False
    is_imessage: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)


class PhoneOutput(ToolOutput):
    phone_id: uuid.UUID
    person_id: uuid.UUID
    e164: str
    is_primary: bool
    status: str = "applied"
    event_id: uuid.UUID | None = None


class PhoneIdInput(BaseModel):
    phone_id: uuid.UUID
    confidence: float = Field(default=1.0, ge=0, le=1)


class RemovePhoneInput(PhoneIdInput):
    reason: Literal[
        "disconnected", "reassigned", "wrong_person", "duplicate", "do_not_call", "other"
    ]


class RemovePhoneOutput(ToolOutput):
    phone_id: uuid.UUID
    removed_at: datetime
    status: str
    event_id: uuid.UUID | None = None


class UpdatePhoneInput(BaseModel):
    phone_id: uuid.UUID
    etag: str
    type: Literal["mobile", "home", "work", "fax", "main", "other"] | None = None
    label: str | None = Field(default=None, max_length=40)
    is_sms_capable: bool | None = None
    is_whatsapp: bool | None = None
    is_signal: bool | None = None
    is_imessage: bool | None = None
    opted_out_sms: bool | None = None
    do_not_call: bool | None = None
    carrier: str | None = None
    line_type: Literal["mobile", "landline", "voip", "toll_free", "satellite", "unknown"] | None = (
        None
    )
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdatePhoneOutput(ToolOutput):
    phone_id: uuid.UUID
    etag: str
    changed_fields: list[str]
    status: str
    event_id: uuid.UUID | None = None


def _normalize(raw: str, country: str) -> NormalizePhoneOutput:
    try:
        parsed = phonenumbers.parse(raw, country)
    except NumberParseException as exc:
        return NormalizePhoneOutput(valid=False, error=str(exc))
    if not phonenumbers.is_valid_number(parsed):
        return NormalizePhoneOutput(valid=False, error="not a valid phone number")
    return NormalizePhoneOutput(
        e164=phonenumbers.format_number(parsed, PhoneNumberFormat.E164),
        national_number=str(parsed.national_number),
        country_code=parsed.country_code,
        type_guess="unknown",
        valid=True,
    )


def _etag(row: Phone) -> str:
    return row.updated_at.isoformat() if row.updated_at else str(row.id)


def _payload(row: Phone) -> dict[str, Any]:
    return {
        "phone_id": row.id,
        "person_id": row.person_id,
        "e164": row.e164,
        "extension": row.extension,
        "type": row.type.value,
        "label": row.label,
        "is_primary": row.is_primary,
        "valid_until": row.valid_until,
        "etag": _etag(row),
    }


async def _load_phone(ctx: MCPContext, phone_id: uuid.UUID) -> Phone:
    row = await ctx.db.get(Phone, phone_id)
    if row is None or row.person_id is None:
        raise ToolError(VALIDATION_FAILED, "phone not found")
    await load_person(ctx, row.person_id)
    return row


async def _demote(ctx: MCPContext, person_id: uuid.UUID, keep_id: uuid.UUID | None = None) -> None:
    rows = (
        await ctx.db.execute(
            select(Phone).where(Phone.person_id == person_id, Phone.is_primary.is_(True))
        )
    ).scalars()
    for row in rows:
        if keep_id is None or row.id != keep_id:
            row.is_primary = False


async def normalize_phone(ctx: MCPContext, req: NormalizePhoneInput) -> NormalizePhoneOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["phone:normalize"])
    return _normalize(req.raw, req.default_country)


async def add_phone(ctx: MCPContext, req: AddPhoneInput) -> PhoneOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["phone:write"])
    await load_person(ctx, req.person_id)
    norm = _normalize(req.e164 or req.raw or "", req.default_country)
    if not norm.valid or norm.e164 is None:
        raise ToolError(VALIDATION_FAILED, norm.error or "phone number is not parseable")
    existing = await ctx.db.scalar(
        select(Phone).where(Phone.person_id == req.person_id, Phone.e164 == norm.e164)
    )
    if existing:
        return PhoneOutput(
            phone_id=existing.id,
            person_id=req.person_id,
            e164=existing.e164,
            is_primary=existing.is_primary,
        )
    other = await ctx.db.scalar(
        select(Phone.id).where(Phone.e164 == norm.e164, Phone.person_id != req.person_id)
    )
    if other:
        raise ToolError(DUPLICATE_RECORD, "phone already belongs to another person")
    is_primary = req.is_primary or not await ctx.db.scalar(
        select(Phone.id).where(Phone.person_id == req.person_id).limit(1)
    )
    if is_primary:
        await _demote(ctx, req.person_id)
    row = Phone(
        person_id=req.person_id,
        e164=norm.e164,
        extension=req.extension,
        type=PhoneType(req.type),
        label=req.label,
        is_primary=is_primary,
        is_sms_capable=req.is_sms_capable,
        is_whatsapp=req.is_whatsapp,
        is_signal=req.is_signal,
        is_imessage=req.is_imessage,
        country_code=norm.country_code,
        national_number=norm.national_number,
        confidence=Decimal(str(req.confidence)),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="phone.add",
        aggregate_type="phone",
        aggregate_id=row.id,
        affected_ids=[req.person_id],
        payload_before=None,
        payload_after=_payload(row),
        confidence=req.confidence,
    )
    return PhoneOutput(
        phone_id=row.id,
        person_id=req.person_id,
        e164=row.e164,
        is_primary=row.is_primary,
        event_id=event_id,
    )


async def remove_phone(ctx: MCPContext, req: RemovePhoneInput) -> RemovePhoneOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["phone:write"])
    row = await _load_phone(ctx, req.phone_id)
    before = _payload(row)
    row.valid_until = now_utc()
    row.is_primary = False
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="phone.remove",
        aggregate_type="phone",
        aggregate_id=row.id,
        affected_ids=[row.person_id] if row.person_id else [],
        payload_before=before,
        payload_after={**_payload(row), "reason": req.reason},
        confidence=req.confidence,
    )
    return RemovePhoneOutput(
        phone_id=row.id, removed_at=row.valid_until, status="applied", event_id=event_id
    )


async def set_primary_phone(ctx: MCPContext, req: PhoneIdInput) -> PhoneOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["phone:write"])
    row = await _load_phone(ctx, req.phone_id)
    assert row.person_id is not None
    if row.valid_until is not None:
        raise ToolError(VALIDATION_FAILED, "removed phone cannot be primary")
    before = _payload(row)
    await _demote(ctx, row.person_id, row.id)
    row.is_primary = True
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="phone.primary_set",
        aggregate_type="phone",
        aggregate_id=row.id,
        affected_ids=[row.person_id] if row.person_id else [],
        payload_before=before,
        payload_after=_payload(row),
        confidence=req.confidence,
    )
    return PhoneOutput(
        phone_id=row.id, person_id=row.person_id, e164=row.e164, is_primary=True, event_id=event_id
    )


async def update_phone(ctx: MCPContext, req: UpdatePhoneInput) -> UpdatePhoneOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["phone:write"])
    row = await _load_phone(ctx, req.phone_id)
    if _etag(row) != req.etag:
        raise ToolError(VALIDATION_FAILED, "stale phone etag")
    before = _payload(row)
    changed: list[str] = []
    patch = req.model_dump(exclude={"phone_id", "etag", "confidence"}, exclude_unset=True)
    for field, value in patch.items():
        if field == "type" and value is not None:
            value = PhoneType(value)
        if field == "line_type" and value is not None:
            value = LineType(value)
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed.append(field)
    await ctx.db.flush()
    event_id = None
    if changed:
        event_id = await emit_action_event(
            ctx,
            event_type="phone.update",
            aggregate_type="phone",
            aggregate_id=row.id,
            affected_ids=[row.person_id] if row.person_id else [],
            payload_before=before,
            payload_after=_payload(row),
            confidence=req.confidence,
        )
    return UpdatePhoneOutput(
        phone_id=row.id,
        etag=_etag(row),
        changed_fields=changed,
        status="applied",
        event_id=event_id,
    )


for _name, _input, _output, _handler, _role, _scopes, _readonly in [
    ("add_phone", AddPhoneInput, PhoneOutput, add_phone, "STAFF", ("phone:write",), False),
    (
        "remove_phone",
        RemovePhoneInput,
        RemovePhoneOutput,
        remove_phone,
        "STAFF",
        ("phone:write",),
        False,
    ),
    (
        "set_primary_phone",
        PhoneIdInput,
        PhoneOutput,
        set_primary_phone,
        "STAFF",
        ("phone:write",),
        False,
    ),
    (
        "update_phone",
        UpdatePhoneInput,
        UpdatePhoneOutput,
        update_phone,
        "STAFF",
        ("phone:write",),
        False,
    ),
    (
        "normalize_phone",
        NormalizePhoneInput,
        NormalizePhoneOutput,
        normalize_phone,
        "CLIENT",
        ("phone:normalize",),
        True,
    ),
]:
    register(
        name=_name,
        description=(
            f"{_name.replace('_', ' ')} using libphonenumber normalization, "
            "tenant RBAC, structured validation, and action_event audit for mutations."
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
        idempotency="natural-key" if _name == "add_phone" else "none",
    )
