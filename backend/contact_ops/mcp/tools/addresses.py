from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, Field
from sqlalchemy import select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, now_utc, register
from contact_ops.mcp.tools.orgs import _load_org
from contact_ops.models import PostalAddress
from contact_ops.models.enums import AddressType, AddressVerifiedVia, GeoPrecision


class AddAddressInput(BaseModel):
    subject_kind: Literal["person", "org"]
    subject_id: uuid.UUID
    type: Literal["home", "work", "billing", "shipping", "mailing", "other"] = "home"
    label: str | None = Field(default=None, max_length=40)
    is_primary: bool = False
    po_box: str | None = Field(default=None, max_length=40)
    extended_address: str | None = Field(default=None, max_length=200)
    street_address: str | None = Field(default=None, max_length=300)
    locality: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    region_code: str | None = Field(default=None, max_length=20)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    country_name: str | None = Field(default=None, max_length=120)
    geo_lat: Decimal | None = Field(default=None, ge=-90, le=90)
    geo_lng: Decimal | None = Field(default=None, ge=-180, le=180)
    confidence: float = Field(default=1.0, ge=0, le=1)


class AddressOutput(ToolOutput):
    address_id: uuid.UUID
    subject_kind: str
    subject_id: uuid.UUID
    is_primary: bool
    status: str = "applied"
    event_id: uuid.UUID | None = None


class AddressIdInput(BaseModel):
    address_id: uuid.UUID
    confidence: float = Field(default=1.0, ge=0, le=1)


class RemoveAddressInput(AddressIdInput):
    reason: Literal["moved", "duplicate", "wrong", "other"]


class RemoveAddressOutput(ToolOutput):
    address_id: uuid.UUID
    removed_at: datetime
    status: str
    event_id: uuid.UUID | None = None


class UpdateAddressInput(BaseModel):
    address_id: uuid.UUID
    etag: str
    type: Literal["home", "work", "billing", "shipping", "mailing", "other"] | None = None
    label: str | None = Field(default=None, max_length=40)
    is_primary: bool | None = None
    po_box: str | None = Field(default=None, max_length=40)
    extended_address: str | None = Field(default=None, max_length=200)
    street_address: str | None = Field(default=None, max_length=300)
    locality: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    region_code: str | None = Field(default=None, max_length=20)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    country_name: str | None = Field(default=None, max_length=120)
    geo_lat: Decimal | None = Field(default=None, ge=-90, le=90)
    geo_lng: Decimal | None = Field(default=None, ge=-180, le=180)
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdateAddressOutput(ToolOutput):
    address_id: uuid.UUID
    etag: str
    changed_fields: list[str]
    status: str
    event_id: uuid.UUID | None = None


class GeocodeAddressInput(BaseModel):
    address_id: uuid.UUID | None = None
    street_address: str | None = None
    locality: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class GeocodeAddressOutput(ToolOutput):
    address_id: uuid.UUID | None = None
    geo_lat: Decimal | None = None
    geo_lng: Decimal | None = None
    precision: Literal["unknown"] = "unknown"


def _subject(row: PostalAddress) -> tuple[str, uuid.UUID]:
    if row.person_id is not None:
        return "person", row.person_id
    if row.organization_id is not None:
        return "org", row.organization_id
    raise ToolError(VALIDATION_FAILED, "address has no subject")


def _cols(kind: str, subject_id: uuid.UUID) -> dict[str, uuid.UUID | None]:
    return {
        "person_id": subject_id if kind == "person" else None,
        "organization_id": subject_id if kind == "org" else None,
    }


def _etag(row: PostalAddress) -> str:
    return row.updated_at.isoformat() if row.updated_at else str(row.id)


def _payload(row: PostalAddress) -> dict[str, Any]:
    kind, subject_id = _subject(row)
    return {
        "address_id": row.id,
        "subject_kind": kind,
        "subject_id": subject_id,
        "street_address": row.street_address,
        "locality": row.locality,
        "region": row.region,
        "postal_code": row.postal_code,
        "country_code": row.country_code,
        "is_primary": row.is_primary,
        "valid_until": row.valid_until,
        "etag": _etag(row),
    }


async def _assert_subject(ctx: MCPContext, kind: str, subject_id: uuid.UUID) -> None:
    if kind == "person":
        await load_person(ctx, subject_id)
    else:
        await _load_org(ctx, subject_id)


async def _load_address(ctx: MCPContext, address_id: uuid.UUID) -> PostalAddress:
    row = await ctx.db.get(PostalAddress, address_id)
    if row is None:
        raise ToolError(VALIDATION_FAILED, "address not found")
    kind, subject_id = _subject(row)
    await _assert_subject(ctx, kind, subject_id)
    return row


async def _demote(
    ctx: MCPContext, kind: str, subject_id: uuid.UUID, keep_id: uuid.UUID | None = None
) -> None:
    rows = (
        await ctx.db.execute(
            select(PostalAddress).where(
                PostalAddress.person_id == (subject_id if kind == "person" else None),
                PostalAddress.organization_id == (subject_id if kind == "org" else None),
                PostalAddress.is_primary.is_(True),
            )
        )
    ).scalars()
    for row in rows:
        if keep_id is None or row.id != keep_id:
            row.is_primary = False


async def add_address(ctx: MCPContext, req: AddAddressInput) -> AddressOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["address:write"])
    await _assert_subject(ctx, req.subject_kind, req.subject_id)
    is_primary = req.is_primary or not await ctx.db.scalar(
        select(PostalAddress.id)
        .where(
            PostalAddress.person_id == (req.subject_id if req.subject_kind == "person" else None),
            PostalAddress.organization_id
            == (req.subject_id if req.subject_kind == "org" else None),
        )
        .limit(1)
    )
    if is_primary:
        await _demote(ctx, req.subject_kind, req.subject_id)
    row = PostalAddress(
        **_cols(req.subject_kind, req.subject_id),
        type=AddressType(req.type),
        label=req.label,
        is_primary=is_primary,
        po_box=req.po_box,
        extended_address=req.extended_address,
        street_address=req.street_address,
        locality=req.locality,
        region=req.region,
        region_code=req.region_code,
        postal_code=req.postal_code,
        country_code=req.country_code,
        country_name=req.country_name,
        geo_lat=req.geo_lat,
        geo_lng=req.geo_lng,
        geo_precision=GeoPrecision.unknown,
        confidence=Decimal(str(req.confidence)),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="address.add",
        aggregate_type="address",
        aggregate_id=row.id,
        affected_ids=[req.subject_id],
        payload_before=None,
        payload_after=_payload(row),
        confidence=req.confidence,
    )
    return AddressOutput(
        address_id=row.id,
        subject_kind=req.subject_kind,
        subject_id=req.subject_id,
        is_primary=row.is_primary,
        event_id=event_id,
    )


async def remove_address(ctx: MCPContext, req: RemoveAddressInput) -> RemoveAddressOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["address:write"])
    row = await _load_address(ctx, req.address_id)
    before = _payload(row)
    row.valid_until = now_utc()
    row.is_primary = False
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="address.remove",
        aggregate_type="address",
        aggregate_id=row.id,
        affected_ids=[_subject(row)[1]],
        payload_before=before,
        payload_after={**_payload(row), "reason": req.reason},
        confidence=req.confidence,
    )
    return RemoveAddressOutput(
        address_id=row.id, removed_at=row.valid_until, status="applied", event_id=event_id
    )


async def set_primary_address(ctx: MCPContext, req: AddressIdInput) -> AddressOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["address:write"])
    row = await _load_address(ctx, req.address_id)
    if row.valid_until is not None:
        raise ToolError(VALIDATION_FAILED, "removed address cannot be primary")
    kind, subject_id = _subject(row)
    before = _payload(row)
    await _demote(ctx, kind, subject_id, row.id)
    row.is_primary = True
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="address.primary_set",
        aggregate_type="address",
        aggregate_id=row.id,
        affected_ids=[subject_id],
        payload_before=before,
        payload_after=_payload(row),
        confidence=req.confidence,
    )
    return AddressOutput(
        address_id=row.id,
        subject_kind=kind,
        subject_id=subject_id,
        is_primary=True,
        event_id=event_id,
    )


async def update_address(ctx: MCPContext, req: UpdateAddressInput) -> UpdateAddressOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["address:write"])
    row = await _load_address(ctx, req.address_id)
    if _etag(row) != req.etag:
        raise ToolError(VALIDATION_FAILED, "stale address etag")
    before = _payload(row)
    changed: list[str] = []
    patch = req.model_dump(
        exclude={"address_id", "etag", "confidence", "subject_kind", "subject_id"},
        exclude_unset=True,
    )
    for field, value in patch.items():
        if field == "type" and value is not None:
            value = AddressType(value)
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed.append(field)
    await ctx.db.flush()
    event_id = None
    if changed:
        event_id = await emit_action_event(
            ctx,
            event_type="address.update",
            aggregate_type="address",
            aggregate_id=row.id,
            affected_ids=[_subject(row)[1]],
            payload_before=before,
            payload_after=_payload(row),
            confidence=req.confidence,
        )
    return UpdateAddressOutput(
        address_id=row.id,
        etag=_etag(row),
        changed_fields=changed,
        status="applied",
        event_id=event_id,
    )


async def geocode_address(ctx: MCPContext, req: GeocodeAddressInput) -> GeocodeAddressOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["address:write", "address:geocode"])
    if req.address_id is not None:
        row = await _load_address(ctx, req.address_id)
        row.verified_via = AddressVerifiedVia.unverified
    return GeocodeAddressOutput(address_id=req.address_id)


for _name, _input, _output, _handler, _readonly in [
    ("add_address", AddAddressInput, AddressOutput, add_address, False),
    ("remove_address", RemoveAddressInput, RemoveAddressOutput, remove_address, False),
    ("update_address", UpdateAddressInput, UpdateAddressOutput, update_address, False),
    ("set_primary_address", AddressIdInput, AddressOutput, set_primary_address, False),
    ("geocode_address", GeocodeAddressInput, GeocodeAddressOutput, geocode_address, True),
]:
    register(
        name=_name,
        description=(
            f"{_name.replace('_', ' ')} for vCard-style postal addresses with "
            "tenant RBAC and audited state changes; geocoding is a Phase 1 stub."
        ),
        input_model=cast(type[BaseModel], _input),
        output_model=cast(type[BaseModel], _output),
        handler=_handler,
        required_role="STAFF",
        required_scopes=("address:write", "address:geocode")
        if _name == "geocode_address"
        else ("address:write",),
        annotations={
            "readOnlyHint": _readonly,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": _name == "geocode_address",
        },
        idempotency="natural-key" if _name == "add_address" else "none",
    )
