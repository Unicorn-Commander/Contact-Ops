from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.im_handles import AddImHandleInput, add_im_handle


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


def test_im_handle_tools_registered() -> None:
    register_all_tools()
    for name in {"add_im_handle", "update_im_handle", "remove_im_handle"}:
        assert get_tool(name) is not None


def test_add_im_handle_input_validation() -> None:
    with pytest.raises(ValueError):
        AddImHandleInput(person_id=uuid.uuid4(), protocol="", handle="x")
    with pytest.raises(ValueError):
        AddImHandleInput(person_id=uuid.uuid4(), protocol="instagram", handle="")


@pytest.mark.asyncio
async def test_add_im_handle_rbac_rejects_client() -> None:
    req = AddImHandleInput(person_id=uuid.uuid4(), protocol="instagram", handle="@diesel")
    with pytest.raises(ToolError) as exc:
        await add_im_handle(_ctx(), req)
    assert exc.value.code == INSUFFICIENT_ROLE
