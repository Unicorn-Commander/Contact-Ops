from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.addresses import AddAddressInput, add_address


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


def test_address_tools_registered() -> None:
    register_all_tools()
    for name in {"add_address", "remove_address", "update_address", "set_primary_address", "geocode_address"}:
        assert get_tool(name) is not None


def test_add_address_geo_validation() -> None:
    with pytest.raises(ValueError):
        AddAddressInput(subject_kind="person", subject_id=uuid.uuid4(), geo_lat=100)


@pytest.mark.asyncio
async def test_add_address_rbac_rejects_client() -> None:
    req = AddAddressInput(subject_kind="person", subject_id=uuid.uuid4(), street_address="1 Main St")
    with pytest.raises(ToolError) as exc:
        await add_address(_ctx(), req)
    assert exc.value.code == INSUFFICIENT_ROLE
