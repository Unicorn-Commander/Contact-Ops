"""Non-DB guards for org/tenant optimistic-concurrency etag handling.

``update_organization`` and ``update_tenant_settings`` used to raise
``VALIDATION_FAILED`` when the caller's etag did not match the stored row,
violating the repo policy that lost-update races return ``STALE_ETAG``
(matching ``update_person`` and the im_handle/url/identifier tools). These tests
drive the two guards with a fake DB (no Postgres) and assert:

  * a stale etag is rejected with STALE_ETAG before any mutation;
  * missing rows still raise ORG_NOT_FOUND / TENANT_NOT_FOUND, i.e. nothing was over-reclassified.

The DB-backed paths (touch_updated trigger bumping updated_at on UPDATE) remain
covered by the testcontainer migration roundtrip suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from contact_ops.mcp.errors import (
    ORG_NOT_FOUND,
    STALE_ETAG,
    ToolError,
)
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.orgs import UpdateOrganizationInput, update_organization
from contact_ops.mcp.tools.tenancy import (
    TENANT_NOT_FOUND,
    UpdateTenantSettingsInput,
    update_tenant_settings,
)
from contact_ops.models import Organization, Tenant
from contact_ops.models.enums import MergeStatus, OrgKind, TenantKind

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _FakeDb:
    """Answers ``get(Model, id)`` from a per-model row map; everything else no-ops."""

    def __init__(self, rows: dict[type, Any]) -> None:
        self._rows = rows

    async def get(self, model: type, _ident: uuid.UUID) -> Any:
        return self._rows.get(model)

    async def scalar(self, _statement: Any) -> Any:
        # Current main refreshes a tenant under SELECT FOR UPDATE.
        return self._rows.get(Tenant)

    async def flush(self) -> None:
        return None


def _ctx(db: _FakeDb, roles: str, scopes: str) -> MCPContext:
    return MCPContext(
        tenant_id=TENANT,
        user_id="test-user",
        actor_chain={"sub": "test-user"},
        human_authority=str(TENANT),
        db=db,  # type: ignore[arg-type]
        audit_db=db,  # type: ignore[arg-type]
        request_id="test-request",
        claims={"realm_access": {"roles": roles.split()}, "scope": scopes},
    )


def _org(etag: str = "live-etag") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        canonical_owner_tenant_id=TENANT,
        etag=etag,
        legal_name="Magic Unicorn LLC",
        display_name="Magic Unicorn",
        kind=OrgKind("company"),
        domain=None,
        industry=None,
        merge_status=MergeStatus("canonical"),
        created_at=NOW,
        updated_at=NOW,
    )


def _tenant(etag: str = "live-etag") -> SimpleNamespace:
    return SimpleNamespace(
        id=TENANT,
        slug="magic-unicorn",
        kind=TenantKind("personal"),
        display_name="Magic Unicorn",
        owner_user_id=uuid.uuid4(),
        parent_tenant_id=None,
        hipaa_mode=False,
        gdpr_mode=False,
        data_residency="us-east",
        carddav_enabled=False,
        data_intel_publish_consent=False,
        graph_mode="per_org_graph",
        graph_name=None,
        qdrant_namespace="q",
        garage_bucket_prefix="g",
        branding={},
        retention_policy={},
        etag=etag,
        created_at=NOW,
        updated_at=NOW,
        deprecated_at=None,
    )


# ── stale etag rejected with STALE_ETAG (not VALIDATION_FAILED) ─────────────


@pytest.mark.asyncio
async def test_update_organization_rejects_stale_etag_as_stale_etag() -> None:
    org = _org()
    ctx = _ctx(_FakeDb({Organization: org}), "STAFF", "org:write")
    req = UpdateOrganizationInput(org_id=org.id, etag="stale")

    with pytest.raises(ToolError) as exc:
        await update_organization(ctx, req)
    assert exc.value.code == STALE_ETAG


@pytest.mark.asyncio
async def test_update_tenant_settings_rejects_stale_etag_as_stale_etag() -> None:
    tenant = _tenant()
    ctx = _ctx(_FakeDb({Tenant: tenant}), "ADMIN", "tenant:write")
    req = UpdateTenantSettingsInput(tenant_id=TENANT, etag="stale")

    with pytest.raises(ToolError) as exc:
        await update_tenant_settings(ctx, req)
    assert exc.value.code == STALE_ETAG


# ── genuine validation errors are NOT reclassified ──────────────────────────


@pytest.mark.asyncio
async def test_update_organization_missing_row_is_not_found() -> None:
    ctx = _ctx(_FakeDb({}), "STAFF", "org:write")
    req = UpdateOrganizationInput(org_id=uuid.uuid4(), etag="anything")

    with pytest.raises(ToolError) as exc:
        await update_organization(ctx, req)
    assert exc.value.code == ORG_NOT_FOUND


@pytest.mark.asyncio
async def test_update_tenant_settings_missing_row_is_not_found() -> None:
    ctx = _ctx(_FakeDb({}), "ADMIN", "tenant:write")
    req = UpdateTenantSettingsInput(tenant_id=TENANT, etag="anything")

    with pytest.raises(ToolError) as exc:
        await update_tenant_settings(ctx, req)
    assert exc.value.code == TENANT_NOT_FOUND


# ── matching etag does not trip the guard ───────────────────────────────────


@pytest.mark.asyncio
async def test_update_organization_matching_etag_no_changes_applied() -> None:
    org = _org(etag="live-etag")
    ctx = _ctx(_FakeDb({Organization: org}), "STAFF", "org:write")
    req = UpdateOrganizationInput(org_id=org.id, etag="live-etag")

    result = await update_organization(ctx, req)
    assert result.status == "applied"
    assert result.etag == "live-etag"
    assert result.changed_fields == []


@pytest.mark.asyncio
async def test_update_tenant_settings_matching_etag_no_changes_applied() -> None:
    tenant = _tenant(etag="live-etag")
    ctx = _ctx(_FakeDb({Tenant: tenant}), "ADMIN", "tenant:write")
    req = UpdateTenantSettingsInput(tenant_id=TENANT, etag="live-etag")

    result = await update_tenant_settings(ctx, req)
    assert result.status == "applied"
    assert result.etag == "live-etag"
    assert result.changed_fields == []
