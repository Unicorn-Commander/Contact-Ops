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


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeDb:
    """Answers list_tenants' queries in order: home tenants, memberships, extras."""

    def __init__(self, *results: list[Any]) -> None:
        self._results = list(results)

    async def execute(self, *_a: Any, **_k: Any) -> _Result:
        return _Result(self._results.pop(0))


def _tenant(tid: uuid.UUID, slug: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        id=tid, slug=slug, kind=SimpleNamespace(value="brand"), display_name=slug.title(),
        owner_user_id=uuid.uuid4(), parent_tenant_id=None, hipaa_mode=False, gdpr_mode=False,
        data_residency=None, carddav_enabled=False, data_intel_publish_consent=False,
        deprecated_at=None, created_at=None, updated_at=None, etag="e", graph_mode=None,
        graph_name=None, qdrant_namespace=None, garage_bucket_prefix=None, plan_tier=None,
        isolation_mode=None, retention_policy={}, branding={},
    )


@pytest.mark.asyncio
async def test_list_tenants_reports_role_on_home_workspace() -> None:
    home_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    other_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    ctx = _ctx("CLIENT", "tenant:read")
    ctx.db = cast(Any, _FakeDb(
        [_tenant(home_id, "home")],
        [(home_id, "admin"), (other_id, "member")],
        [_tenant(other_id, "other")],
    ))
    out = await list_tenants(ctx, ListTenantsInput())
    roles = {item["tenant_id"]: item.get("role") for item in out.items}
    assert roles == {str(home_id): "admin", str(other_id): "member"}
