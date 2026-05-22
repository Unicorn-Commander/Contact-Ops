from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.employment import SetEmploymentInput, set_employment


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


def test_employment_tools_registered() -> None:
    register_all_tools()
    for name in {"set_employment", "end_employment", "list_employments", "update_employment"}:
        assert get_tool(name) is not None


def test_set_employment_validates_ownership_percent() -> None:
    req = SetEmploymentInput(
        person_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        role_type="employee",
        ownership_percent=10,
    )
    assert req.ownership_percent == 10


@pytest.mark.asyncio
async def test_set_employment_rbac_rejects_client() -> None:
    req = SetEmploymentInput(person_id=uuid.uuid4(), org_id=uuid.uuid4(), role_type="employee")
    with pytest.raises(ToolError) as exc:
        await set_employment(_ctx(), req)
    assert exc.value.code == INSUFFICIENT_ROLE
