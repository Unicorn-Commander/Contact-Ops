from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.emails import AddEmailInput, add_email


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


def test_email_tools_registered() -> None:
    register_all_tools()
    for name in {"add_email", "remove_email", "set_primary_email", "update_email", "verify_email"}:
        assert get_tool(name) is not None


def test_add_email_validation_and_normalization() -> None:
    req = AddEmailInput(person_id=uuid.uuid4(), address="REMOVED")
    assert req.address == "REMOVED"
    with pytest.raises(ValueError):
        AddEmailInput(person_id=uuid.uuid4(), address="not-email")


@pytest.mark.asyncio
async def test_add_email_rbac_rejects_client() -> None:
    req = AddEmailInput(person_id=uuid.uuid4(), address="REMOVED")
    with pytest.raises(ToolError) as exc:
        await add_email(_ctx(), req)
    assert exc.value.code == INSUFFICIENT_ROLE
