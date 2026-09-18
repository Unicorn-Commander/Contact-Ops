from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import STALE_ETAG, VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, now_utc, register
from contact_ops.models import IMHandle

# The `im_handles.protocol` column is free text (no enum). Suite convention keeps
# the value a short lowercase slug (instagram, tiktok, x, facebook, threads,
# youtube, discord, telegram, signal, whatsapp, …); anything else is accepted and
# lowercased, but not validated against a fixed set.


class AddImHandleInput(BaseModel):
    person_id: uuid.UUID
    protocol: str = Field(min_length=1, max_length=40)
    handle: str = Field(min_length=1, max_length=400)
    label: str | None = Field(default=None, max_length=120)
    is_primary: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)


class ImHandleOutput(ToolOutput):
    etag: str
    im_handle_id: uuid.UUID
    person_id: uuid.UUID
    protocol: str
    handle: str
    label: str | None = None
    is_primary: bool
    status: str = "applied"
    event_id: uuid.UUID | None = None


class UpdateImHandleInput(BaseModel):
    im_handle_id: uuid.UUID
    etag: str
    protocol: str | None = Field(default=None, min_length=1, max_length=40)
    handle: str | None = Field(default=None, min_length=1, max_length=400)
    label: str | None = Field(default=None, max_length=120)
    is_primary: bool | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdateImHandleOutput(ToolOutput):
    im_handle_id: uuid.UUID
    etag: str
    changed_fields: list[str]
    status: str
    event_id: uuid.UUID | None = None


class RemoveImHandleInput(BaseModel):
    im_handle_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=400)
    confidence: float = Field(default=1.0, ge=0, le=1)


class RemoveImHandleOutput(ToolOutput):
    im_handle_id: uuid.UUID
    removed_at: datetime
    status: str
    event_id: uuid.UUID | None = None


def _payload(row: IMHandle) -> dict[str, Any]:
    return {
        "im_handle_id": row.id,
        "person_id": row.person_id,
        "protocol": row.protocol,
        "handle": row.handle,
        "label": row.label,
        "is_primary": row.is_primary,
    }


def _etag(row: IMHandle) -> str:
    return row.updated_at.isoformat() if row.updated_at else str(row.id)


async def _load_im_handle(ctx: MCPContext, im_handle_id: uuid.UUID) -> IMHandle:
    row = await ctx.db.get(IMHandle, im_handle_id)
    if row is None or row.person_id is None:
        raise ToolError(VALIDATION_FAILED, "im_handle not found")
    await load_person(ctx, row.person_id)
    return row


async def _demote(ctx: MCPContext, person_id: uuid.UUID, keep_id: uuid.UUID | None = None) -> None:
    rows = (
        await ctx.db.execute(
            select(IMHandle).where(IMHandle.person_id == person_id, IMHandle.is_primary.is_(True))
        )
    ).scalars()
    for row in rows:
        if keep_id is None or row.id != keep_id:
            row.is_primary = False


async def add_im_handle(ctx: MCPContext, req: AddImHandleInput) -> ImHandleOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["person:write"])
    await load_person(ctx, req.person_id)
    protocol = req.protocol.strip().lower()
    handle = req.handle.strip()
    existing = await ctx.db.scalar(
        select(IMHandle).where(
            IMHandle.person_id == req.person_id,
            func.lower(IMHandle.protocol) == protocol,
            func.lower(IMHandle.handle) == handle.lower(),
        )
    )
    if existing:
        return ImHandleOutput(
            im_handle_id=existing.id,
            etag=_etag(existing),
            person_id=req.person_id,
            protocol=existing.protocol,
            handle=existing.handle,
            label=existing.label,
            is_primary=existing.is_primary,
        )
    # A protocol:handle pair is unique per person only; the same handle on another
    # person is a different contact point and is allowed (unlike email/phone).
    is_primary = req.is_primary or not await ctx.db.scalar(
        select(IMHandle.id).where(IMHandle.person_id == req.person_id).limit(1)
    )
    if is_primary:
        await _demote(ctx, req.person_id)
    row = IMHandle(
        person_id=req.person_id,
        protocol=protocol,
        handle=handle,
        label=req.label,
        is_primary=is_primary,
        confidence=Decimal(str(req.confidence)),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="im_handle.add",
        aggregate_type="person",
        aggregate_id=req.person_id,
        affected_ids=[req.person_id],
        payload_before=None,
        payload_after=_payload(row),
        confidence=req.confidence,
    )
    return ImHandleOutput(
        im_handle_id=row.id,
        etag=_etag(row),
        person_id=req.person_id,
        protocol=row.protocol,
        handle=row.handle,
        label=row.label,
        is_primary=row.is_primary,
        event_id=event_id,
    )


async def update_im_handle(ctx: MCPContext, req: UpdateImHandleInput) -> UpdateImHandleOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["person:write"])
    row = await _load_im_handle(ctx, req.im_handle_id)
    assert row.person_id is not None
    await ctx.db.refresh(row, with_for_update=True)
    if _etag(row) != req.etag:
        raise ToolError(STALE_ETAG, "stale im_handle etag", retryable=False)
    before = _payload(row)
    changed: list[str] = []
    patch = req.model_dump(exclude={"im_handle_id", "etag", "confidence"}, exclude_unset=True)
    if "protocol" in patch and patch["protocol"] is not None:
        patch["protocol"] = patch["protocol"].strip().lower()
    if "handle" in patch and patch["handle"] is not None:
        patch["handle"] = patch["handle"].strip()
    # protocol/handle are NOT NULL — an explicit null means "no change", never a
    # write of NULL. `label` stays nullable so a null clears it.
    for required in ("protocol", "handle"):
        if patch.get(required) is None:
            patch.pop(required, None)
    for field, value in patch.items():
        if field == "is_primary":
            if value is None:
                continue
            if value and not row.is_primary:
                await _demote(ctx, row.person_id, row.id)
                row.is_primary = True
                changed.append(field)
            elif not value and row.is_primary:
                row.is_primary = False
                changed.append(field)
            continue
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed.append(field)
    await ctx.db.flush()
    # The `touch_updated` trigger bumps `updated_at` on UPDATE at the DB level, so
    # the in-memory row is one write behind. Refresh to capture the post-write
    # value before we serialize the etag back to the client.
    await ctx.db.refresh(row)
    event_id = None
    if changed:
        event_id = await emit_action_event(
            ctx,
            event_type="im_handle.update",
            aggregate_type="person",
            aggregate_id=row.person_id,
            affected_ids=[row.person_id],
            payload_before=before,
            payload_after=_payload(row),
            confidence=req.confidence,
        )
    return UpdateImHandleOutput(
        im_handle_id=row.id,
        etag=_etag(row),
        changed_fields=changed,
        status="applied",
        event_id=event_id,
    )


async def remove_im_handle(ctx: MCPContext, req: RemoveImHandleInput) -> RemoveImHandleOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["person:write"])
    row = await _load_im_handle(ctx, req.im_handle_id)
    assert row.person_id is not None
    before = _payload(row)
    removed_at = now_utc()
    await ctx.db.delete(row)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="im_handle.remove",
        aggregate_type="person",
        aggregate_id=row.person_id,
        affected_ids=[row.person_id],
        payload_before=before,
        payload_after={"removed_at": removed_at.isoformat(), "reason": req.reason},
        confidence=req.confidence,
    )
    return RemoveImHandleOutput(
        im_handle_id=req.im_handle_id, removed_at=removed_at, status="applied", event_id=event_id
    )


for _name, _input, _output, _handler, _scopes in [
    ("add_im_handle", AddImHandleInput, ImHandleOutput, add_im_handle, ("person:write",)),
    (
        "update_im_handle",
        UpdateImHandleInput,
        UpdateImHandleOutput,
        update_im_handle,
        ("person:write",),
    ),
    (
        "remove_im_handle",
        RemoveImHandleInput,
        RemoveImHandleOutput,
        remove_im_handle,
        ("person:write",),
    ),
]:
    register(
        name=_name,
        description=(
            f"{_name.replace('_', ' ')} for a person's IM/social handle "
            "(protocol, handle, label, is_primary) with tenant-scoped RBAC, workspace "
            "isolation via load_person, and action_event audit emission."
        ),
        input_model=cast(type[BaseModel], _input),
        output_model=cast(type[BaseModel], _output),
        handler=_handler,
        required_role="STAFF",
        required_scopes=_scopes,
        annotations={
            "readOnlyHint": False,
            "destructiveHint": _name == "remove_im_handle",
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key" if _name == "add_im_handle" else "none",
    )
