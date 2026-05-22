"""Photo MCP tools (Phase 2 Codex 1).

Two-step upload flow:

1. ``upload_photo`` reserves a media_asset + photo row in a pending state and
   returns a presigned PUT URL plus the storage bucket/key. The client (a
   browser, mobile app, or another agent) PUTs the bytes directly to Garage.

2. ``confirm_photo_upload`` verifies the blob's presence and size via HEAD,
   records the final sha256/byte_size on the media_asset, optionally promotes
   the photo to primary, and emits the action_event. Idempotent on
   (asset_id, sha256).

Photos are person-only at the DB level (see ``photos`` schema). Organization
logos go through ``organizations.logo_asset_id`` which is out of Phase 2 scope.
"""
# ruff: noqa: I001

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import (
    PERSON_NOT_FOUND,
    VALIDATION_FAILED,
    ToolError,
)
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, register
from contact_ops.models import MediaAsset, Person, Photo, Tenant
from contact_ops.models.enums import RetentionClass
from contact_ops.services.storage import StorageService, get_storage_service

PHOTO_NOT_FOUND = "PHOTO_NOT_FOUND"
ALREADY_REMOVED = "ALREADY_REMOVED"
UPLOAD_NOT_PRESENT = "UPLOAD_NOT_PRESENT"
UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"

ALLOWED_PHOTO_TYPES = (
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/webp",
    "image/gif",
)
PHOTO_REMOVE_REASONS = ("wrong_person", "bad_quality", "outdated", "other")
PHOTO_BUCKET_KIND = "photo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _tenant_slug(ctx: MCPContext) -> str:
    tenant = await ctx.db.get(Tenant, ctx.tenant_id)
    if tenant is None:
        raise ToolError(VALIDATION_FAILED, "caller tenant not found")
    return tenant.slug


def _media_asset_payload(asset: MediaAsset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "tenant_id": str(asset.tenant_id),
        "bucket": asset.bucket,
        "object_key": asset.object_key,
        "content_type": asset.content_type,
        "byte_size": asset.byte_size,
        "sha256": asset.sha256.hex() if asset.sha256 else None,
        "kind": asset.kind,
        "captured_at": asset.captured_at,
        "retention_class": asset.retention_class.value,
    }


def _photo_payload(photo: Photo) -> dict[str, Any]:
    return {
        "photo_id": str(photo.id),
        "asset_id": str(photo.asset_id),
        "person_id": str(photo.person_id),
        "is_primary": photo.is_primary,
        "observed_at": photo.observed_at,
        "quality_score": photo.quality_score,
        "is_redacted": photo.is_redacted,
    }


# ---------------------------------------------------------------------------
# upload_photo (prepare)
# ---------------------------------------------------------------------------


class UploadPhotoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID
    content_type: Literal[ALLOWED_PHOTO_TYPES]  # type: ignore[valid-type]
    byte_size: int = Field(ge=1, le=20 * 1024 * 1024)
    sha256_hex: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F]{64}$"
    )
    is_primary: bool = False
    captured_at: datetime | None = None
    source_label: str | None = Field(default=None, max_length=120)


class UploadPhotoOutput(ToolOutput):
    photo_id: uuid.UUID
    asset_id: uuid.UUID
    bucket: str
    object_key: str
    upload_url: str
    upload_url_expires_seconds: int
    is_primary_requested: bool
    status: str


def _ext_for_content_type(content_type: str) -> str:
    mapping = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/heic": "heic",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    return mapping.get(content_type, "bin")


async def upload_photo(
    ctx: MCPContext, req: UploadPhotoInput
) -> UploadPhotoOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["media:write"])
    person = await load_person(ctx, req.person_id)
    if person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PERSON_NOT_FOUND, "person not in caller's tenant")

    slug = await _tenant_slug(ctx)
    storage = get_storage_service()
    asset_id = uuid.uuid4()
    photo_id = uuid.uuid4()
    bucket = storage.photo_bucket(slug)
    object_key = (
        f"{req.person_id}/{asset_id}.{_ext_for_content_type(req.content_type)}"
    )

    sha_bytes = (
        bytes.fromhex(req.sha256_hex) if req.sha256_hex else bytes(32)
    )
    asset = MediaAsset(
        id=asset_id,
        tenant_id=ctx.tenant_id,
        bucket=bucket,
        object_key=object_key,
        content_type=req.content_type,
        byte_size=req.byte_size,
        sha256=sha_bytes,
        kind=PHOTO_BUCKET_KIND,
        captured_at=req.captured_at,
        retention_class=RetentionClass.operational_2y,
    )
    photo = Photo(
        id=photo_id,
        asset_id=asset_id,
        person_id=req.person_id,
        is_primary=False,  # promoted after confirm
        observed_at=req.captured_at or datetime.now().astimezone(),
        is_redacted=False,
    )
    ctx.db.add(asset)
    ctx.db.add(photo)
    await ctx.db.flush()

    upload_url = await storage.presigned_put_url(
        bucket=bucket, key=object_key, content_type=req.content_type
    )
    return UploadPhotoOutput(
        photo_id=photo_id,
        asset_id=asset_id,
        bucket=bucket,
        object_key=object_key,
        upload_url=upload_url,
        upload_url_expires_seconds=storage.DEFAULT_PUT_TTL_SECONDS,
        is_primary_requested=req.is_primary,
        status="pending_upload",
    )


# ---------------------------------------------------------------------------
# confirm_photo_upload
# ---------------------------------------------------------------------------


class ConfirmPhotoUploadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    photo_id: uuid.UUID
    sha256_hex: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    promote_to_primary: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)


class ConfirmPhotoUploadOutput(ToolOutput):
    photo_id: uuid.UUID
    asset_id: uuid.UUID
    is_primary: bool
    byte_size: int
    status: str
    event_id: uuid.UUID | None = None


async def confirm_photo_upload(
    ctx: MCPContext, req: ConfirmPhotoUploadInput
) -> ConfirmPhotoUploadOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["media:write"])
    photo = await ctx.db.get(Photo, req.photo_id)
    if photo is None:
        raise ToolError(PHOTO_NOT_FOUND, "photo not found")
    asset = await ctx.db.get(MediaAsset, photo.asset_id)
    if asset is None or asset.tenant_id != ctx.tenant_id:
        raise ToolError(PHOTO_NOT_FOUND, "photo asset not found in tenant")

    storage = get_storage_service()
    head = await storage.head(bucket=asset.bucket, key=asset.object_key)
    if head is None:
        raise ToolError(
            UPLOAD_NOT_PRESENT,
            "no object found at the presigned-upload location",
            hint="re-PUT the bytes before calling confirm_photo_upload",
        )
    actual_size = int(head.get("size") or 0)
    if actual_size <= 0 or actual_size > 20 * 1024 * 1024:
        raise ToolError(
            VALIDATION_FAILED,
            f"uploaded object has invalid size: {actual_size}",
        )

    before = _photo_payload(photo)
    sha_bytes = bytes.fromhex(req.sha256_hex)
    asset.sha256 = sha_bytes
    asset.byte_size = actual_size

    if req.promote_to_primary:
        await ctx.db.execute(
            update(Photo)
            .where(Photo.person_id == photo.person_id, Photo.id != photo.id)
            .values(is_primary=False)
        )
        photo.is_primary = True
    elif not await ctx.db.scalar(
        select(Photo.id).where(
            Photo.person_id == photo.person_id, Photo.is_primary.is_(True)
        )
    ):
        # First photo for the person becomes primary by default.
        photo.is_primary = True
    await ctx.db.flush()

    event_id = await emit_action_event(
        ctx,
        event_type="photo.confirm",
        aggregate_type="person",
        aggregate_id=photo.person_id,
        affected_ids=[photo.person_id],
        payload_before=before,
        payload_after={
            **_photo_payload(photo),
            "asset": _media_asset_payload(asset),
        },
        confidence=req.confidence,
    )
    return ConfirmPhotoUploadOutput(
        photo_id=photo.id,
        asset_id=asset.id,
        is_primary=photo.is_primary,
        byte_size=asset.byte_size,
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# set_primary_photo
# ---------------------------------------------------------------------------


class SetPrimaryPhotoInput(BaseModel):
    photo_id: uuid.UUID
    confidence: float = Field(default=1.0, ge=0, le=1)


class SetPrimaryPhotoOutput(ToolOutput):
    photo_id: uuid.UUID
    person_id: uuid.UUID
    previous_primary_photo_id: uuid.UUID | None
    status: str
    event_id: uuid.UUID | None = None


async def set_primary_photo(
    ctx: MCPContext, req: SetPrimaryPhotoInput
) -> SetPrimaryPhotoOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["media:write"])
    photo = await ctx.db.get(Photo, req.photo_id)
    if photo is None:
        raise ToolError(PHOTO_NOT_FOUND, "photo not found")
    person = await ctx.db.get(Person, photo.person_id)
    if person is None or person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PHOTO_NOT_FOUND, "photo not in caller's tenant")

    previous_primary_id = await ctx.db.scalar(
        select(Photo.id).where(
            Photo.person_id == photo.person_id,
            Photo.is_primary.is_(True),
            Photo.id != photo.id,
        )
    )
    if photo.is_primary and previous_primary_id is None:
        return SetPrimaryPhotoOutput(
            photo_id=photo.id,
            person_id=photo.person_id,
            previous_primary_photo_id=None,
            status="noop",
        )
    await ctx.db.execute(
        update(Photo)
        .where(Photo.person_id == photo.person_id, Photo.id != photo.id)
        .values(is_primary=False)
    )
    photo.is_primary = True
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="photo.set_primary",
        aggregate_type="person",
        aggregate_id=photo.person_id,
        affected_ids=[photo.person_id],
        payload_before={
            "previous_primary_photo_id": (
                str(previous_primary_id) if previous_primary_id else None
            ),
        },
        payload_after=_photo_payload(photo),
        confidence=req.confidence,
    )
    return SetPrimaryPhotoOutput(
        photo_id=photo.id,
        person_id=photo.person_id,
        previous_primary_photo_id=previous_primary_id,
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# list_photos
# ---------------------------------------------------------------------------


class ListPhotosInput(BaseModel):
    person_id: uuid.UUID
    include_redacted: bool = False
    limit: int = Field(default=10, ge=1, le=50)


class ListPhotosOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int


async def list_photos(
    ctx: MCPContext, req: ListPhotosInput
) -> ListPhotosOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["media:read"])
    person = await ctx.db.get(Person, req.person_id)
    if person is None or person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PERSON_NOT_FOUND, "person not found in tenant")

    stmt = (
        select(Photo, MediaAsset)
        .join(MediaAsset, MediaAsset.id == Photo.asset_id)
        .where(Photo.person_id == req.person_id)
        .order_by(Photo.is_primary.desc(), Photo.observed_at.desc())
        .limit(req.limit)
    )
    if not req.include_redacted:
        stmt = stmt.where(Photo.is_redacted.is_(False))
    rows = (await ctx.db.execute(stmt)).all()
    storage = get_storage_service()
    items: list[dict[str, Any]] = []
    for photo, asset in rows:
        download_url = await storage.presigned_get_url(
            bucket=asset.bucket, key=asset.object_key
        )
        items.append(
            {
                "photo_id": str(photo.id),
                "asset_id": str(asset.id),
                "is_primary": photo.is_primary,
                "content_type": asset.content_type,
                "byte_size": asset.byte_size,
                "captured_at": asset.captured_at,
                "observed_at": photo.observed_at,
                "download_url": download_url,
                "download_url_expires_seconds": storage.DEFAULT_GET_TTL_SECONDS,
                "is_redacted": photo.is_redacted,
                "quality_score": photo.quality_score,
            }
        )
    return ListPhotosOutput(items=items, count=len(items))


# ---------------------------------------------------------------------------
# remove_photo
# ---------------------------------------------------------------------------


class RemovePhotoInput(BaseModel):
    photo_id: uuid.UUID
    reason: Literal[PHOTO_REMOVE_REASONS] = "other"  # type: ignore[valid-type]
    confidence: float = Field(default=1.0, ge=0, le=1)


class RemovePhotoOutput(ToolOutput):
    photo_id: uuid.UUID
    removed_at: datetime
    new_primary_photo_id: uuid.UUID | None
    status: str
    event_id: uuid.UUID | None = None


async def remove_photo(
    ctx: MCPContext, req: RemovePhotoInput
) -> RemovePhotoOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["media:write"])
    photo = await ctx.db.get(Photo, req.photo_id)
    if photo is None:
        raise ToolError(PHOTO_NOT_FOUND, "photo not found")
    person = await ctx.db.get(Person, photo.person_id)
    if person is None or person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PHOTO_NOT_FOUND, "photo not in caller's tenant")
    if photo.is_redacted:
        raise ToolError(ALREADY_REMOVED, "photo already removed")

    before = _photo_payload(photo)
    photo.is_redacted = True
    if photo.is_primary:
        photo.is_primary = False
    await ctx.db.flush()

    # auto-promote the next-newest photo for the same person
    next_primary_id = await ctx.db.scalar(
        select(Photo.id)
        .where(
            Photo.person_id == photo.person_id,
            Photo.is_redacted.is_(False),
            Photo.id != photo.id,
        )
        .order_by(Photo.observed_at.desc())
        .limit(1)
    )
    if next_primary_id is not None:
        await ctx.db.execute(
            update(Photo)
            .where(Photo.id == next_primary_id)
            .values(is_primary=True)
        )
    removed_at = datetime.now().astimezone()
    event_id = await emit_action_event(
        ctx,
        event_type="photo.remove",
        aggregate_type="person",
        aggregate_id=photo.person_id,
        affected_ids=[photo.person_id],
        payload_before=before,
        payload_after={
            **_photo_payload(photo),
            "reason": req.reason,
            "new_primary_photo_id": (
                str(next_primary_id) if next_primary_id else None
            ),
            "removed_at": removed_at.isoformat(),
        },
        confidence=req.confidence,
    )
    return RemovePhotoOutput(
        photo_id=photo.id,
        removed_at=removed_at,
        new_primary_photo_id=next_primary_id,
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


def register_photo_tools() -> None:
    register(
        name="upload_photo",
        description=(
            "Reserve a media_asset + photo row and return a presigned PUT URL "
            "for the client to upload the bytes. Requires STAFF and media:write."
        ),
        input_model=UploadPhotoInput,
        output_model=UploadPhotoOutput,
        handler=upload_photo,
        required_role="STAFF",
        required_scopes=("media:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        idempotency="none",
    )
    register(
        name="confirm_photo_upload",
        description=(
            "Verify the uploaded blob exists in Garage, record sha256/size, "
            "and emit the photo.confirm action_event. Requires STAFF and "
            "media:write."
        ),
        input_model=ConfirmPhotoUploadInput,
        output_model=ConfirmPhotoUploadOutput,
        handler=confirm_photo_upload,
        required_role="STAFF",
        required_scopes=("media:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        idempotency="natural-key",
    )
    register(
        name="set_primary_photo",
        description=(
            "Promote a photo to primary for its subject person. Atomic "
            "demote/promote. Requires STAFF and media:write."
        ),
        input_model=SetPrimaryPhotoInput,
        output_model=SetPrimaryPhotoOutput,
        handler=set_primary_photo,
        required_role="STAFF",
        required_scopes=("media:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
    register(
        name="list_photos",
        description=(
            "List photos for a person with presigned GET URLs. Requires "
            "CLIENT and media:read."
        ),
        input_model=ListPhotosInput,
        output_model=ListPhotosOutput,
        handler=list_photos,
        required_role="CLIENT",
        required_scopes=("media:read",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        idempotency="none",
    )
    register(
        name="remove_photo",
        description=(
            "Soft-remove a photo (redact + auto-promote next). Bytes remain "
            "in Garage for retention purge. Requires STAFF and media:write."
        ),
        input_model=RemovePhotoInput,
        output_model=RemovePhotoOutput,
        handler=remove_photo,
        required_role="STAFF",
        required_scopes=("media:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )


register_photo_tools()


# silence unused-import warnings for symbols we re-export for tests
_ = (StorageService, base64, hashlib, UNSUPPORTED_CONTENT_TYPE)
