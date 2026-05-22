from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.acl import (
    ListAclInput,
    RevokeShareInput,
    SetVisibilityInput,
    ShareWithUserInput,
    list_acl,
    revoke_share,
    set_visibility,
    share_with_user,
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


def test_acl_tools_registered() -> None:
    register_all_tools()
    for name in {"set_visibility", "list_acl", "share_with_user", "revoke_share"}:
        assert get_tool(name) is not None, f"{name} should be registered"


def test_share_with_user_requires_exactly_one_grantee() -> None:
    with pytest.raises(ValueError):
        ShareWithUserInput(
            subject_kind="person",
            subject_id=uuid.uuid4(),
            scope="read",
        )
    with pytest.raises(ValueError):
        ShareWithUserInput(
            subject_kind="person",
            subject_id=uuid.uuid4(),
            grantee_user_id=uuid.uuid4(),
            grantee_tenant_id=uuid.uuid4(),
            scope="read",
        )
    ok = ShareWithUserInput(
        subject_kind="person",
        subject_id=uuid.uuid4(),
        grantee_user_id=uuid.uuid4(),
        scope="read",
    )
    assert ok.scope == "read"


@pytest.mark.asyncio
async def test_share_with_user_requires_manager() -> None:
    req = ShareWithUserInput(
        subject_kind="person",
        subject_id=uuid.uuid4(),
        grantee_user_id=uuid.uuid4(),
        scope="read",
    )
    with pytest.raises(ToolError) as exc:
        await share_with_user(_ctx("STAFF", "acl:write"), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_revoke_share_requires_manager() -> None:
    with pytest.raises(ToolError) as exc:
        await revoke_share(
            _ctx("STAFF", "acl:write"),
            RevokeShareInput(acl_id=uuid.uuid4()),
        )
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_set_visibility_requires_staff() -> None:
    req = SetVisibilityInput(
        person_id=uuid.uuid4(), tenant_id=uuid.uuid4(), visibility="hidden"
    )
    with pytest.raises(ToolError) as exc:
        await set_visibility(_ctx("CLIENT", "membership:write"), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_list_acl_requires_acl_read_scope() -> None:
    with pytest.raises(ToolError):
        await list_acl(
            _ctx("STAFF", ""),
            ListAclInput(subject_kind="person", subject_id=uuid.uuid4()),
        )


def test_scope_enum_locked_to_read_write_merge() -> None:
    with pytest.raises(ValueError):
        ShareWithUserInput(
            subject_kind="person",
            subject_id=uuid.uuid4(),
            grantee_user_id=uuid.uuid4(),
            scope="admin",  # type: ignore[arg-type]
        )
