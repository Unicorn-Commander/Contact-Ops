from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.identifiers import AddIdentifierInput, add_identifier


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


def test_identifier_tools_registered() -> None:
    register_all_tools()
    for name in {"add_identifier", "remove_identifier", "list_identifiers", "verify_identifier"}:
        assert get_tool(name) is not None


def test_add_identifier_input_validation() -> None:
    with pytest.raises(ValueError):
        AddIdentifierInput(subject_kind="person", subject_id=uuid.uuid4(), namespace="", value="x")


@pytest.mark.asyncio
async def test_add_identifier_rbac_rejects_client() -> None:
    req = AddIdentifierInput(
        subject_kind="person",
        subject_id=uuid.uuid4(),
        namespace="github.com",
        value="aaron",
    )
    with pytest.raises(ToolError) as exc:
        await add_identifier(_ctx(), req)
    assert exc.value.code == INSUFFICIENT_ROLE
