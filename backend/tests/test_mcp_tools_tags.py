from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.tags import TagPersonInput, tag_person


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


def test_tag_tools_registered() -> None:
    register_all_tools()
    for name in {"tag_person", "untag_person", "list_tags", "bulk_tag", "search_by_tag"}:
        assert get_tool(name) is not None


def test_tag_normalization() -> None:
    req = TagPersonInput(person_id=uuid.uuid4(), tags=["Investor Prospect", "investor-prospect"])
    assert req.tags == ["investor-prospect", "investor-prospect"]
    with pytest.raises(ValueError):
        TagPersonInput(person_id=uuid.uuid4(), tags=["bad/tag"])


@pytest.mark.asyncio
async def test_tag_person_rbac_rejects_client() -> None:
    req = TagPersonInput(person_id=uuid.uuid4(), tags=["Investor"])
    with pytest.raises(ToolError) as exc:
        await tag_person(_ctx(), req)
    assert exc.value.code == INSUFFICIENT_ROLE
