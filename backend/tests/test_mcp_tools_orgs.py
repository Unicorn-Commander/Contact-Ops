from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.orgs import CreateOrganizationInput, create_organization


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


def test_organization_tools_registered() -> None:
    register_all_tools()
    for name in {
        "create_organization",
        "upsert_organization",
        "get_organization",
        "update_organization",
        "archive_organization",
        "list_organizations",
        "search_organizations",
        "find_organization_by_identifier",
    }:
        assert get_tool(name) is not None


def test_create_organization_input_validation() -> None:
    with pytest.raises(ValueError):
        CreateOrganizationInput(legal_name="Bad Co", domain="not a host")


@pytest.mark.asyncio
async def test_create_organization_rbac_rejects_client() -> None:
    with pytest.raises(ToolError) as exc:
        await create_organization(_ctx(), CreateOrganizationInput(legal_name="Magic Unicorn LLC"))
    assert exc.value.code == INSUFFICIENT_ROLE
