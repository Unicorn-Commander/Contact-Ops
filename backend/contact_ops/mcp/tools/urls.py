from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, now_utc, register
from contact_ops.models import Url


class AddUrlInput(BaseModel):
    person_id: uuid.UUID
    url: str = Field(min_length=1, max_length=2000)
    type: str = Field(default="profile", min_length=1, max_length=40)
    label: str | None = Field(default=None, max_length=120)
    is_primary: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)


class UrlOutput(ToolOutput):
    url_id: uuid.UUID
    person_id: uuid.UUID
    url: str
    type: str
    label: str | None = None
    is_primary: bool
    status: str = "applied"
    event_id: uuid.UUID | None = None


class UpdateUrlInput(BaseModel):
    url_id: uuid.UUID
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    type: str | None = Field(default=None, min_length=1, max_length=40)
    label: str | None = Field(default=None, max_length=120)
    is_primary: bool | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdateUrlOutput(ToolOutput):
    url_id: uuid.UUID
    changed_fields: list[str]
    status: str
    event_id: uuid.UUID | None = None


class RemoveUrlInput(BaseModel):
    url_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=400)
    confidence: float = Field(default=1.0, ge=0, le=1)


class RemoveUrlOutput(ToolOutput):
    url_id: uuid.UUID
    removed_at: datetime
    status: str
    event_id: uuid.UUID | None = None


def _payload(row: Url) -> dict[str, Any]:
    return {
        "url_id": row.id,
        "person_id": row.person_id,
        "url": row.url,
        "type": row.type,
        "label": row.label,
        "is_primary": row.is_primary,
    }


async def _load_url(ctx: MCPContext, url_id: uuid.UUID) -> Url:
    row = await ctx.db.get(Url, url_id)
    if row is None or row.person_id is None:
        raise ToolError(VALIDATION_FAILED, "url not found")
    await load_person(ctx, row.person_id)
    return row


async def _demote(ctx: MCPContext, person_id: uuid.UUID, keep_id: uuid.UUID | None = None) -> None:
    rows = (
        await ctx.db.execute(
            select(Url).where(Url.person_id == person_id, Url.is_primary.is_(True))
        )
    ).scalars()
    for row in rows:
        if keep_id is None or row.id != keep_id:
            row.is_primary = False


async def add_url(ctx: MCPContext, req: AddUrlInput) -> UrlOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["person:write"])
    await load_person(ctx, req.person_id)
    url = req.url.strip()
    existing = await ctx.db.scalar(
        select(Url).where(Url.person_id == req.person_id, func.lower(Url.url) == url.lower())
    )
    if existing:
        return UrlOutput(
            url_id=existing.id,
            person_id=req.person_id,
            url=existing.url,
            type=existing.type,
            label=existing.label,
            is_primary=existing.is_primary,
        )
    is_primary = req.is_primary or not await ctx.db.scalar(
        select(Url.id).where(Url.person_id == req.person_id).limit(1)
    )
    if is_primary:
        await _demote(ctx, req.person_id)
    row = Url(
        person_id=req.person_id,
        url=url,
        type=req.type.strip().lower(),
        label=req.label,
        is_primary=is_primary,
        confidence=Decimal(str(req.confidence)),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="url.add",
        aggregate_type="person",
        aggregate_id=req.person_id,
        affected_ids=[req.person_id],
        payload_before=None,
        payload_after=_payload(row),
        confidence=req.confidence,
    )
    return UrlOutput(
        url_id=row.id,
        person_id=req.person_id,
        url=row.url,
        type=row.type,
        label=row.label,
        is_primary=row.is_primary,
        event_id=event_id,
    )


async def update_url(ctx: MCPContext, req: UpdateUrlInput) -> UpdateUrlOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["person:write"])
    row = await _load_url(ctx, req.url_id)
    assert row.person_id is not None
    before = _payload(row)
    changed: list[str] = []
    patch = req.model_dump(exclude={"url_id", "confidence"}, exclude_unset=True)
    if "url" in patch and patch["url"] is not None:
        patch["url"] = patch["url"].strip()
    if "type" in patch and patch["type"] is not None:
        patch["type"] = patch["type"].strip().lower()
    # url/type are NOT NULL — an explicit null means "no change", never a write of
    # NULL. `label` stays nullable so a null clears it.
    for required in ("url", "type"):
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
    event_id = None
    if changed:
        event_id = await emit_action_event(
            ctx,
            event_type="url.update",
            aggregate_type="person",
            aggregate_id=row.person_id,
            affected_ids=[row.person_id],
            payload_before=before,
            payload_after=_payload(row),
            confidence=req.confidence,
        )
    return UpdateUrlOutput(
        url_id=row.id, changed_fields=changed, status="applied", event_id=event_id
    )


async def remove_url(ctx: MCPContext, req: RemoveUrlInput) -> RemoveUrlOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["person:write"])
    row = await _load_url(ctx, req.url_id)
    assert row.person_id is not None
    before = _payload(row)
    removed_at = now_utc()
    await ctx.db.delete(row)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="url.remove",
        aggregate_type="person",
        aggregate_id=row.person_id,
        affected_ids=[row.person_id],
        payload_before=before,
        payload_after={"removed_at": removed_at.isoformat(), "reason": req.reason},
        confidence=req.confidence,
    )
    return RemoveUrlOutput(
        url_id=req.url_id, removed_at=removed_at, status="applied", event_id=event_id
    )


for _name, _input, _output, _handler, _scopes in [
    ("add_url", AddUrlInput, UrlOutput, add_url, ("person:write",)),
    ("update_url", UpdateUrlInput, UpdateUrlOutput, update_url, ("person:write",)),
    ("remove_url", RemoveUrlInput, RemoveUrlOutput, remove_url, ("person:write",)),
]:
    register(
        name=_name,
        description=(
            f"{_name.replace('_', ' ')} for a person's URL (url, type, label, "
            "is_primary) with tenant-scoped RBAC, workspace isolation via load_person, "
            "and action_event audit emission."
        ),
        input_model=cast(type[BaseModel], _input),
        output_model=cast(type[BaseModel], _output),
        handler=_handler,
        required_role="STAFF",
        required_scopes=_scopes,
        annotations={
            "readOnlyHint": False,
            "destructiveHint": _name == "remove_url",
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key" if _name == "add_url" else "none",
    )
