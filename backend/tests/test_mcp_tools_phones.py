from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.phones import AddPhoneInput, NormalizePhoneInput, add_phone, normalize_phone


def _ctx(role: str = "CLIENT", scopes: str = "phone:normalize") -> MCPContext:
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


def test_phone_tools_registered() -> None:
    register_all_tools()
    for name in {"add_phone", "remove_phone", "set_primary_phone", "update_phone", "normalize_phone"}:
        assert get_tool(name) is not None


@pytest.mark.asyncio
async def test_normalize_phone_valid_and_invalid() -> None:
    good = await normalize_phone(_ctx(), NormalizePhoneInput(raw="(843) 901-9078"))
    assert good.valid is True
    assert good.e164 == "+18439019078"
    bad = await normalize_phone(_ctx(), NormalizePhoneInput(raw="not a phone"))
    assert bad.valid is False


@pytest.mark.asyncio
async def test_add_phone_rbac_rejects_client() -> None:
    req = AddPhoneInput(person_id=uuid.uuid4(), raw="(843) 901-9078")
    with pytest.raises(ToolError) as exc:
        await add_phone(_ctx(), req)
    assert exc.value.code == INSUFFICIENT_ROLE
