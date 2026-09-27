from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, INSUFFICIENT_SCOPE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.person_names import (
    AddPersonNameInput,
    ListPersonNamesInput,
    add_person_name,
    list_person_names,
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


def test_person_name_tools_registered() -> None:
    register_all_tools()
    for name in {
        "add_person_name",
        "remove_person_name",
        "update_person_name",
        "set_primary_person_name",
        "list_person_names",
    }:
        assert get_tool(name) is not None


def test_add_person_name_input_validation() -> None:
    with pytest.raises(ValueError):
        AddPersonNameInput(person_id=uuid.uuid4(), name_type="aka", value="")


def test_add_person_name_rejects_invalid_name_type() -> None:
    with pytest.raises(ValueError):
        AddPersonNameInput(person_id=uuid.uuid4(), name_type="not-a-type", value="x")


@pytest.mark.asyncio
async def test_add_person_name_rbac_rejects_client() -> None:
    req = AddPersonNameInput(person_id=uuid.uuid4(), name_type="aka", value="Bob Smith")
    with pytest.raises(ToolError) as exc:
        await add_person_name(_ctx(), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_list_person_names_rbac_rejects_missing_scope() -> None:
    req = ListPersonNamesInput(person_id=uuid.uuid4())
    # CLIENT role passes the role gate but lacks the required name:read scope.
    with pytest.raises(ToolError) as exc:
        await list_person_names(_ctx(role="CLIENT", scopes=""), req)
    assert exc.value.code == INSUFFICIENT_SCOPE
