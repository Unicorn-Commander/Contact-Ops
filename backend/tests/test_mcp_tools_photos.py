from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.photos import (
    ConfirmPhotoUploadInput,
    ListPhotosInput,
    RemovePhotoInput,
    SetPrimaryPhotoInput,
    UploadPhotoInput,
    confirm_photo_upload,
    list_photos,
    remove_photo,
    set_primary_photo,
    upload_photo,
)


def _ctx(role: str = "CLIENT", scopes: str = "") -> MCPContext:
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    return MCPContext(
        tenant_id=tenant_id,
        user_id="test-user",
        actor_chain={"sub": "test-user"},
        human_authority=str(tenant_id),
        db=cast(Any, None),
        audit_db=cast(Any, None),
        request_id="test-request",
        claims={"realm_access": {"roles": [role]}, "scope": scopes},
    )


def test_photo_tools_registered() -> None:
    register_all_tools()
    for name in {
        "upload_photo",
        "confirm_photo_upload",
        "set_primary_photo",
        "list_photos",
        "remove_photo",
    }:
        assert get_tool(name) is not None, f"{name} should be registered"


def test_upload_photo_rejects_unsupported_content_type() -> None:
    with pytest.raises(ValueError):
        UploadPhotoInput(
            person_id=uuid.uuid4(),
            content_type="image/bmp",  # type: ignore[arg-type]
            byte_size=1024,
        )


def test_upload_photo_enforces_size_ceiling() -> None:
    with pytest.raises(ValueError):
        UploadPhotoInput(
            person_id=uuid.uuid4(),
            content_type="image/jpeg",
            byte_size=21 * 1024 * 1024,
        )


def test_confirm_photo_upload_requires_sha256_hex() -> None:
    with pytest.raises(ValueError):
        ConfirmPhotoUploadInput(photo_id=uuid.uuid4(), sha256_hex="abc")


@pytest.mark.asyncio
async def test_upload_photo_requires_staff() -> None:
    req = UploadPhotoInput(
        person_id=uuid.uuid4(),
        content_type="image/jpeg",
        byte_size=1024,
    )
    with pytest.raises(ToolError) as exc:
        await upload_photo(_ctx("CLIENT", "media:write"), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_confirm_photo_upload_requires_staff() -> None:
    req = ConfirmPhotoUploadInput(
        photo_id=uuid.uuid4(),
        sha256_hex="a" * 64,
    )
    with pytest.raises(ToolError) as exc:
        await confirm_photo_upload(_ctx("CLIENT", "media:write"), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_set_primary_photo_requires_staff() -> None:
    req = SetPrimaryPhotoInput(photo_id=uuid.uuid4())
    with pytest.raises(ToolError) as exc:
        await set_primary_photo(_ctx("CLIENT", "media:write"), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_list_photos_requires_scope() -> None:
    with pytest.raises(ToolError):
        await list_photos(_ctx("CLIENT", ""), ListPhotosInput(person_id=uuid.uuid4()))


@pytest.mark.asyncio
async def test_remove_photo_requires_staff() -> None:
    req = RemovePhotoInput(photo_id=uuid.uuid4(), reason="wrong_person")
    with pytest.raises(ToolError) as exc:
        await remove_photo(_ctx("CLIENT", "media:write"), req)
    assert exc.value.code == INSUFFICIENT_ROLE
