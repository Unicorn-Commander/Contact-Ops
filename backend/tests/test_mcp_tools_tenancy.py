from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.tenancy import (
    CreateTenantInput,
    EndTenantMembershipInput,
    GetTenantInput,
    ListTenantsInput,
    SetOrgTenantMembershipInput,
    SetTenantMembershipInput,
    UpdateTenantSettingsInput,
    create_tenant,
    end_tenant_membership,
    get_tenant,
    list_tenants,
    set_tenant_membership,
    update_tenant_settings,
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


def test_tenancy_tools_registered() -> None:
    register_all_tools()
    for name in {
        "list_tenants",
        "create_tenant",
        "get_tenant",
        "update_tenant_settings",
        "set_tenant_membership",
        "end_tenant_membership",
        "set_organization_tenant_membership",
    }:
        assert get_tool(name) is not None, f"{name} should be registered"


def test_create_tenant_slug_validation() -> None:
    with pytest.raises(ValueError):
        CreateTenantInput(
            slug="A-bad-slug",
            display_name="X",
            owner_user_id=uuid.uuid4(),
        )
    with pytest.raises(ValueError):
        CreateTenantInput(
            slug="1starts-with-digit",
            display_name="X",
            owner_user_id=uuid.uuid4(),
        )
    ok = CreateTenantInput(
        slug="aaron-personal",
        display_name="Aaron Stransky (personal)",
        owner_user_id=uuid.uuid4(),
    )
    assert ok.slug == "aaron-personal"


@pytest.mark.asyncio
async def test_create_tenant_requires_admin() -> None:
    req = CreateTenantInput(
        slug="ab", display_name="X", owner_user_id=uuid.uuid4()
    )
    with pytest.raises(ToolError) as exc:
        await create_tenant(_ctx("STAFF", "tenant:write"), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_list_tenants_requires_scope() -> None:
    with pytest.raises(ToolError):
        await list_tenants(_ctx("CLIENT", ""), ListTenantsInput())


@pytest.mark.asyncio
async def test_update_tenant_settings_requires_admin() -> None:
    with pytest.raises(ToolError) as exc:
        await update_tenant_settings(
            _ctx("STAFF", "tenant:write"),
            UpdateTenantSettingsInput(tenant_id=uuid.uuid4(), etag="x"),
        )
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_set_tenant_membership_requires_staff() -> None:
    req = SetTenantMembershipInput(
        person_id=uuid.uuid4(), tenant_id=uuid.uuid4(), visibility="visible"
    )
    with pytest.raises(ToolError):
        await set_tenant_membership(
            _ctx("CLIENT", "tenant:write membership:write"), req
        )


@pytest.mark.asyncio
async def test_end_tenant_membership_requires_staff() -> None:
    req = EndTenantMembershipInput(
        person_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
    )
    with pytest.raises(ToolError):
        await end_tenant_membership(_ctx("CLIENT", "membership:write"), req)


def test_set_org_tenant_membership_validation() -> None:
    req = SetOrgTenantMembershipInput(
        organization_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        visibility="hidden",
        tags=["partner"],
    )
    assert req.visibility == "hidden"
    assert req.tags == ["partner"]


@pytest.mark.asyncio
async def test_get_tenant_requires_scope() -> None:
    with pytest.raises(ToolError):
        await get_tenant(
            _ctx("CLIENT", ""),
            GetTenantInput(tenant_id=uuid.uuid4()),
        )
