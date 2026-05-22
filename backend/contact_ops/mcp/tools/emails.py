from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import DUPLICATE_RECORD, VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, now_utc, register
from contact_ops.models import Email
from contact_ops.models.enums import EmailDeliverability, EmailType

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AddEmailInput(BaseModel):
    person_id: uuid.UUID
    address: str = Field(min_length=3, max_length=320)
    type: Literal["personal", "work", "school", "other", "alias"] = "other"
    label: str | None = Field(default=None, max_length=40)
    is_primary: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("address")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_RE.match(normalized):
            raise ValueError("invalid email address")
        return normalized


class EmailOutput(ToolOutput):
    email_id: uuid.UUID
    person_id: uuid.UUID
    address: str
    is_primary: bool
    status: str = "applied"
    event_id: uuid.UUID | None = None


class EmailIdInput(BaseModel):
    email_id: uuid.UUID
    confidence: float = Field(default=1.0, ge=0, le=1)


class RemoveEmailInput(EmailIdInput):
    reason: Literal["bounced", "unsubscribed", "wrong_person", "duplicate", "other"]


class RemoveEmailOutput(ToolOutput):
    email_id: uuid.UUID
    removed_at: datetime
    status: str
    event_id: uuid.UUID | None = None


class UpdateEmailInput(BaseModel):
    email_id: uuid.UUID
    etag: str
    type: Literal["personal", "work", "school", "other", "alias"] | None = None
    label: str | None = Field(default=None, max_length=40)
    deliverability_status: (
        Literal["unknown", "valid", "invalid", "risky", "accept_all", "disposable"] | None
    ) = None
    opted_out: bool | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdateEmailOutput(ToolOutput):
    email_id: uuid.UUID
    etag: str
    changed_fields: list[str]
    status: str
    event_id: uuid.UUID | None = None


class VerifyEmailInput(EmailIdInput):
    method: Literal[
        "manual", "delivery_receipt", "click_through", "oauth_confirmed", "sms_pair"
    ] = "manual"


class VerifyEmailOutput(ToolOutput):
    email_id: uuid.UUID
    verified: bool
    verified_via: str
    verified_at: datetime
    event_id: uuid.UUID | None = None


def _etag(row: Email) -> str:
    return row.updated_at.isoformat() if row.updated_at else str(row.id)


def _payload(row: Email) -> dict[str, Any]:
    return {
        "email_id": row.id,
        "person_id": row.person_id,
        "address": row.address,
        "type": row.type.value,
        "label": row.label,
        "is_primary": row.is_primary,
        "is_verified": row.is_verified,
        "verified_at": row.verified_at,
        "deliverability_status": row.deliverability_status.value,
        "opted_out": row.opted_out,
        "valid_until": row.valid_until,
        "etag": _etag(row),
    }


async def _load_email(ctx: MCPContext, email_id: uuid.UUID) -> Email:
    row = await ctx.db.get(Email, email_id)
    if row is None or row.person_id is None:
        raise ToolError(VALIDATION_FAILED, "email not found")
    await load_person(ctx, row.person_id)
    return row


async def _demote(ctx: MCPContext, person_id: uuid.UUID, keep_id: uuid.UUID | None = None) -> None:
    rows = (
        await ctx.db.execute(
            select(Email).where(Email.person_id == person_id, Email.is_primary.is_(True))
        )
    ).scalars()
    for row in rows:
        if keep_id is None or row.id != keep_id:
            row.is_primary = False


async def add_email(ctx: MCPContext, req: AddEmailInput) -> EmailOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["email:write"])
    await load_person(ctx, req.person_id)
    existing = await ctx.db.scalar(
        select(Email).where(
            Email.person_id == req.person_id, func.lower(Email.address) == req.address
        )
    )
    if existing:
        return EmailOutput(
            email_id=existing.id,
            person_id=req.person_id,
            address=existing.address,
            is_primary=existing.is_primary,
        )
    other = await ctx.db.scalar(
        select(Email.id).where(
            func.lower(Email.address) == req.address, Email.person_id != req.person_id
        )
    )
    if other:
        raise ToolError(DUPLICATE_RECORD, "email already belongs to another person")
    if req.is_primary or not await ctx.db.scalar(
        select(Email.id).where(Email.person_id == req.person_id).limit(1)
    ):
        await _demote(ctx, req.person_id)
        req.is_primary = True
    row = Email(
        person_id=req.person_id,
        address=req.address,
        type=EmailType(req.type),
        label=req.label,
        is_primary=req.is_primary,
        confidence=Decimal(str(req.confidence)),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="email.add",
        aggregate_type="email",
        aggregate_id=row.id,
        affected_ids=[req.person_id],
        payload_before=None,
        payload_after=_payload(row),
        confidence=req.confidence,
    )
    return EmailOutput(
        email_id=row.id,
        person_id=req.person_id,
        address=row.address,
        is_primary=row.is_primary,
        event_id=event_id,
    )


async def remove_email(ctx: MCPContext, req: RemoveEmailInput) -> RemoveEmailOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["email:write"])
    row = await _load_email(ctx, req.email_id)
    before = _payload(row)
    row.valid_until = now_utc()
    row.is_primary = False
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="email.remove",
        aggregate_type="email",
        aggregate_id=row.id,
        affected_ids=[row.person_id] if row.person_id else [],
        payload_before=before,
        payload_after={**_payload(row), "reason": req.reason},
        confidence=req.confidence,
    )
    return RemoveEmailOutput(
        email_id=row.id, removed_at=row.valid_until, status="applied", event_id=event_id
    )


async def set_primary_email(ctx: MCPContext, req: EmailIdInput) -> EmailOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["email:write"])
    row = await _load_email(ctx, req.email_id)
    assert row.person_id is not None
    if row.valid_until is not None:
        raise ToolError(VALIDATION_FAILED, "removed email cannot be primary")
    before = _payload(row)
    await _demote(ctx, row.person_id, row.id)
    row.is_primary = True
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="email.primary_set",
        aggregate_type="email",
        aggregate_id=row.id,
        affected_ids=[row.person_id] if row.person_id else [],
        payload_before=before,
        payload_after=_payload(row),
        confidence=req.confidence,
    )
    return EmailOutput(
        email_id=row.id,
        person_id=row.person_id,
        address=row.address,
        is_primary=True,
        event_id=event_id,
    )


async def update_email(ctx: MCPContext, req: UpdateEmailInput) -> UpdateEmailOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["email:write"])
    row = await _load_email(ctx, req.email_id)
    if _etag(row) != req.etag:
        raise ToolError(VALIDATION_FAILED, "stale email etag")
    before = _payload(row)
    changed: list[str] = []
    patch = req.model_dump(exclude={"email_id", "etag", "confidence"}, exclude_unset=True)
    for field, value in patch.items():
        if field == "type" and value is not None:
            value = EmailType(value)
        if field == "deliverability_status" and value is not None:
            value = EmailDeliverability(value)
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed.append(field)
    await ctx.db.flush()
    event_id = None
    if changed:
        event_id = await emit_action_event(
            ctx,
            event_type="email.update",
            aggregate_type="email",
            aggregate_id=row.id,
            affected_ids=[row.person_id] if row.person_id else [],
            payload_before=before,
            payload_after=_payload(row),
            confidence=req.confidence,
        )
    return UpdateEmailOutput(
        email_id=row.id,
        etag=_etag(row),
        changed_fields=changed,
        status="applied",
        event_id=event_id,
    )


async def verify_email(ctx: MCPContext, req: VerifyEmailInput) -> VerifyEmailOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["email:write", "email:verify"])
    row = await _load_email(ctx, req.email_id)
    before = _payload(row)
    row.is_verified = True
    row.verified_at = now_utc()
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="email.verify",
        aggregate_type="email",
        aggregate_id=row.id,
        affected_ids=[row.person_id] if row.person_id else [],
        payload_before=before,
        payload_after={**_payload(row), "verified_via": req.method},
        confidence=req.confidence,
    )
    return VerifyEmailOutput(
        email_id=row.id,
        verified=True,
        verified_via=req.method,
        verified_at=row.verified_at,
        event_id=event_id,
    )


for _name, _input, _output, _handler, _scopes in [
    ("add_email", AddEmailInput, EmailOutput, add_email, ("email:write",)),
    ("remove_email", RemoveEmailInput, RemoveEmailOutput, remove_email, ("email:write",)),
    ("set_primary_email", EmailIdInput, EmailOutput, set_primary_email, ("email:write",)),
    ("update_email", UpdateEmailInput, UpdateEmailOutput, update_email, ("email:write",)),
    (
        "verify_email",
        VerifyEmailInput,
        VerifyEmailOutput,
        verify_email,
        ("email:write", "email:verify"),
    ),
]:
    register(
        name=_name,
        description=(
            f"{_name.replace('_', ' ')} with tenant-scoped RBAC, "
            "evidence-preserving state changes, and action_event audit emission."
        ),
        input_model=cast(type[BaseModel], _input),
        output_model=cast(type[BaseModel], _output),
        handler=_handler,
        required_role="STAFF",
        required_scopes=_scopes,
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key" if _name == "add_email" else "none",
    )
