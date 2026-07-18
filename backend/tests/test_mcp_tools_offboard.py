"""Coverage for the offboard_tenant MCP tool (tenant offboarding).

Proves the admin-scoped offboard: the confirmation-phrase + role guards, the
Postgres teardown (persons purged, memberships revoked, tenant stamped
deprecated + offboard marker), the ``tenant.offboard`` action_event, the
best-effort external cleanup (per-tenant Garage buckets + FalkorDB graph), and
idempotency (a re-run no-ops via the durable marker).
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy import text

from contact_ops.mcp.errors import (
    CONFIRMATION_PHRASE_MISMATCH,
    INSUFFICIENT_ROLE,
    ToolError,
)
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.tenancy import (
    OffboardTenantInput,
    offboard_tenant,
)

# asyncio_mode=auto (see pyproject) runs the async tests without an explicit
# mark; the sync guard tests here must NOT carry a module-level asyncio mark.

_PHRASE = "OFFBOARD TENANT PERMANENTLY"


def _ctx(db, *, tenant_id: uuid.UUID, role: str = "ADMIN", scopes: str = "tenant:write person:delete") -> MCPContext:
    return MCPContext(
        tenant_id=tenant_id,
        user_id=str(uuid.uuid4()),
        actor_chain={"sub": "test-admin"},
        human_authority=str(tenant_id),
        db=db,
        audit_db=db,
        request_id="test-request",
        claims={"realm_access": {"roles": [role]}, "scope": scopes},
    )


class _FakeFalkorClient:
    deleted: list[str] = []
    closed: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def delete_graph(self, graph_name: str) -> bool:
        type(self).deleted.append(graph_name)
        return True

    async def close(self) -> None:
        type(self).closed = True


def test_offboard_tenant_registered() -> None:
    register_all_tools()
    assert get_tool("offboard_tenant") is not None


async def test_offboard_requires_admin() -> None:
    ctx = _ctx(cast(Any, None), tenant_id=uuid.uuid4(), role="STAFF")
    with pytest.raises(ToolError) as exc:
        await offboard_tenant(
            ctx, OffboardTenantInput(confirmation_phrase=_PHRASE, reason="x")
        )
    assert exc.value.code == INSUFFICIENT_ROLE


async def test_offboard_wrong_phrase_rejected() -> None:
    # A Literal-typed field means a wrong phrase fails validation before the
    # handler; assert the model itself refuses it.
    with pytest.raises(ValueError):
        OffboardTenantInput(confirmation_phrase="nope", reason="x")


async def test_offboard_end_to_end_and_idempotent(db_session, seeded_tenants, monkeypatch):
    tid = seeded_tenants["non_hipaa"]
    pid = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO persons (id, canonical_owner_tenant_id, merge_status, "
            "display_name, created_at, updated_at) "
            "VALUES (:id, :tid, 'canonical', 'Doomed Person', now(), now())"
        ),
        {"id": str(pid), "tid": str(tid)},
    )
    await db_session.execute(
        text("INSERT INTO emails (person_id, address, is_primary) VALUES (:p, :a, true)"),
        {"p": str(pid), "a": "REMOVED"},
    )
    await db_session.execute(
        text(
            "INSERT INTO user_tenant_membership (user_uc_uid, tenant_id, role, status) "
            "VALUES ('member-1', :t, 'member', 'active')"
        ),
        {"t": str(tid)},
    )
    # The seed fixture leaves graph_name NULL; set the canonical per-tenant graph
    # so the offboard exercises the FalkorDB drop path.
    await db_session.execute(
        text("UPDATE tenants SET graph_name = 'contact_ops__non_hipaa_org' WHERE id = :t"),
        {"t": str(tid)},
    )
    await db_session.commit()

    # Fake the external planes: record Garage bucket teardown + FalkorDB drop.
    garage_calls: list[str] = []

    async def _fake_delete_buckets(*, tenant_slug: str, settings: Any = None) -> dict:
        garage_calls.append(tenant_slug)
        return {"buckets": {f"contact-ops-photos-{tenant_slug}": "deleted"}}

    monkeypatch.setattr(
        "contact_ops.services.garage_lifecycle.delete_tenant_buckets",
        _fake_delete_buckets,
    )
    _FakeFalkorClient.deleted = []
    _FakeFalkorClient.closed = False
    monkeypatch.setattr(
        "contact_ops.agents.graph_sync.falkordb_client.FalkorDBGraphClient",
        _FakeFalkorClient,
    )

    ctx = _ctx(db_session, tenant_id=tid)
    result = await offboard_tenant(
        ctx, OffboardTenantInput(confirmation_phrase=_PHRASE, reason="contract lost")
    )

    assert result.already_offboarded is False
    assert result.status == "applied"
    assert result.persons_purged == 1
    assert result.memberships_revoked == 1
    assert result.event_id is not None

    # Postgres: person gone, membership revoked, tenant deprecated + marker.
    assert await db_session.scalar(
        text("SELECT count(*) FROM persons WHERE id = :p"), {"p": str(pid)}
    ) == 0
    assert await db_session.scalar(
        text("SELECT status FROM user_tenant_membership WHERE user_uc_uid='member-1' AND tenant_id=:t"),
        {"t": str(tid)},
    ) == "revoked"
    dep, retention = (
        await db_session.execute(
            text("SELECT deprecated_at, retention_policy FROM tenants WHERE id = :t"),
            {"t": str(tid)},
        )
    ).first()
    assert dep is not None
    assert retention["offboarded_at"]
    assert retention["offboard_reason"] == "contract lost"

    # action_event appended.
    assert await db_session.scalar(
        text("SELECT count(*) FROM action_event WHERE aggregate_id=:t AND event_type='tenant.offboard'"),
        {"t": str(tid)},
    ) == 1

    # External planes reached with the tenant slug + graph name.
    assert garage_calls == ["non-hipaa-org"]
    assert result.external["garage"]["buckets"]
    assert _FakeFalkorClient.deleted == ["contact_ops__non_hipaa_org"]
    assert _FakeFalkorClient.closed is True
    assert result.external["falkordb"] == {"graph_deleted": True}

    # Idempotent: a second offboard (no idempotency_key) short-circuits on the
    # durable marker — no double purge, no new event.
    garage_calls.clear()
    second = await offboard_tenant(
        ctx, OffboardTenantInput(confirmation_phrase=_PHRASE, reason="again")
    )
    assert second.already_offboarded is True
    assert second.persons_purged == 0
    assert second.memberships_revoked == 0
    assert garage_calls == []
    assert await db_session.scalar(
        text("SELECT count(*) FROM action_event WHERE aggregate_id=:t AND event_type='tenant.offboard'"),
        {"t": str(tid)},
    ) == 1
