"""Non-DB tests for the REST people surface's billing gates.

``contact_ops.api.people`` delegates the REST im_handle / identifier / url writes
to the same MCP tool handlers, and now re-runs the plan entitlement/quota gates
(``check_entitlement`` + ``check_quota``) that ``server.py`` runs around each MCP
tool call. These tests exercise the WIRING without a database:

  * the denial → HTTP status mapping (402 payment-required for a plan-tier miss,
    403 for an exhausted allowance);
  * that a free tool short-circuits with ZERO plan DB I/O;
  * that a self-host deployment bypasses gating entirely;
  * that a pro tool on a free plan is denied (402) before the handler runs;
  * that a quota denial surfaces as 403.

The DB-backed paths (real RLS + plan rows) live in the testcontainer suite.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from contact_ops.api import people
from contact_ops.mcp.errors import (
    ENTITLEMENT_REQUIRED,
    INSUFFICIENT_ROLE,
    QUOTA_EXCEEDED,
    ToolError,
)
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.im_handles import AddImHandleInput

# _run_tool now resolves the registry ToolDef to enforce RBAC first, so the pro
# tools the tests reference (propose_merge) must be registered.
register_all_tools()


class _FakeDb:
    """RLS-free stand-in that can answer the plan-tier / contact-count probes.

    ``fail_on_scalar`` models the free-tool short-circuit: a free tool must never
    hit the DB, so any scalar call raises.
    """

    def __init__(
        self,
        *,
        plan: str = "free",
        contact_count: int = 0,
        fail_on_scalar: bool = False,
    ) -> None:
        self._plan = plan
        self._count = contact_count
        self._fail = fail_on_scalar

    async def scalar(self, stmt: object, *args: object) -> object:
        if self._fail:
            raise AssertionError("unexpected DB I/O for a free/short-circuit tool")
        sql = str(stmt)
        if "plan_tier" in sql:
            return self._plan
        if "count(*)" in sql:
            return self._count
        raise AssertionError(f"unexpected query: {sql}")

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _SessionCM:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *exc: object) -> bool:
        return False


async def _noop_bind(*_args: object) -> None:
    return None


class _Out:
    def model_dump(self, mode: str = "json") -> dict[str, object]:
        return {"ok": True}


async def _handler(_ctx: object, _req: object) -> _Out:
    return _Out()


def _make_request(
    role: str = "STAFF",
    scopes: str = "person:write dedup:propose",
) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            jwt_claims={
                "tenant_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
                "sub": "u1",
                "uc_uid": "u1",
                "realm_access": {"roles": [role]},
                "scope": scopes,
            },
            actor_chain={"sub": "u1"},
            human_authority="u1",
            request_id="r1",
        ),
        headers={},
    )


def _settings(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "DEPLOYMENT_MODE": "cloud",
        "ENTITLEMENT_ENFORCED": False,
        "BILLING_QUOTA_ENFORCED": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    db: object,
    settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(people, "async_session_maker", lambda: _SessionCM(db))
    monkeypatch.setattr(people, "audit_session_maker", lambda: _SessionCM(_FakeDb()))
    monkeypatch.setattr(people, "bind_session_context", _noop_bind)
    monkeypatch.setattr(people, "get_settings", lambda: settings)


def _payload() -> dict[str, object]:
    return {
        "person_id": uuid.uuid4(),
        "protocol": "instagram",
        "handle": "@diesel",
    }


# ── denial → HTTP status mapping ────────────────────────────────────────────


def test_tool_error_status_maps_billing_denials() -> None:
    assert people._TOOL_ERROR_STATUS[ENTITLEMENT_REQUIRED] == 402
    assert people._TOOL_ERROR_STATUS[QUOTA_EXCEEDED] == 403
    assert people._TOOL_ERROR_STATUS[INSUFFICIENT_ROLE] == 403


def test_raise_tool_error_maps_entitlement_to_402() -> None:
    with pytest.raises(HTTPException) as exc:
        people._raise_tool_error(ToolError(ENTITLEMENT_REQUIRED, "needs pro", retryable=False))
    assert exc.value.status_code == 402


def test_raise_tool_error_maps_quota_to_403() -> None:
    with pytest.raises(HTTPException) as exc:
        people._raise_tool_error(ToolError(QUOTA_EXCEEDED, "over cap", retryable=False))
    assert exc.value.status_code == 403


# ── gate wiring through _run_tool ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_free_tool_bypasses_with_zero_db_io(monkeypatch: pytest.MonkeyPatch) -> None:
    # A free tool (add_im_handle) must never resolve a plan; any DB probe fails.
    db = _FakeDb(fail_on_scalar=True)
    _install_fakes(monkeypatch, db=db, settings=_settings())

    result = await people._run_tool(
        _make_request(),
        AddImHandleInput,
        _handler,
        _payload(),
        tool_name="add_im_handle",
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_self_host_bypasses_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    # Self-host operators get everything; even a pro tool must not resolve a plan.
    db = _FakeDb(fail_on_scalar=True)
    _install_fakes(monkeypatch, db=db, settings=_settings(DEPLOYMENT_MODE="self_host"))

    result = await people._run_tool(
        _make_request(),
        AddImHandleInput,
        _handler,
        _payload(),
        tool_name="propose_merge",
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_pro_tool_on_free_plan_denied_402(monkeypatch: pytest.MonkeyPatch) -> None:
    # pro tool + free plan + enforcement on => ENTITLEMENT_REQUIRED before handler.
    db = _FakeDb(plan="free")
    _install_fakes(
        monkeypatch,
        db=db,
        settings=_settings(ENTITLEMENT_ENFORCED=True),
    )

    with pytest.raises(HTTPException) as exc:
        await people._run_tool(
            _make_request(),
            AddImHandleInput,
            _handler,
            _payload(),
            tool_name="propose_merge",
        )
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_quota_denial_surfaces_as_403(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the quota gate to deny and assert the wiring maps it to 403 without
    # ever invoking the handler.
    async def _deny_quota(*_args: object, **_kwargs: object) -> ToolError:
        return ToolError(QUOTA_EXCEEDED, "over cap", retryable=False)

    async def _allow_entitlement(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("contact_ops.mcp.entitlement.check_entitlement", _allow_entitlement)
    monkeypatch.setattr("contact_ops.billing.check_quota", _deny_quota)
    _install_fakes(monkeypatch, db=_FakeDb(), settings=_settings())

    with pytest.raises(HTTPException) as exc:
        await people._run_tool(
            _make_request(),
            AddImHandleInput,
            _handler,
            _payload(),
            tool_name="add_im_handle",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_unauthorized_role_rejected_403_before_billing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A CLIENT caller on a STAFF tool must be rejected with 403 by RBAC BEFORE any
    # billing probe: both gates are wired to record any invocation, and RBAC-first
    # must reject the caller without ever touching them.
    calls: list[str] = []

    async def _record_probe(*_args: object, **_kwargs: object) -> None:
        calls.append("billing")
        return None

    monkeypatch.setattr("contact_ops.mcp.entitlement.check_entitlement", _record_probe)
    monkeypatch.setattr("contact_ops.billing.check_quota", _record_probe)
    _install_fakes(monkeypatch, db=_FakeDb(), settings=_settings())

    with pytest.raises(HTTPException) as exc:
        await people._run_tool(
            _make_request(role="CLIENT"),
            AddImHandleInput,
            _handler,
            _payload(),
            tool_name="add_im_handle",
        )
    assert exc.value.status_code == 403
    assert calls == []


@pytest.mark.asyncio
async def test_nested_update_rejects_role_before_resource_lookup(monkeypatch):
    from contact_ops.models import IMHandle

    class NoLookupDb(_FakeDb):
        async def get(self, *args):
            raise AssertionError("resource existence probed before RBAC")

    _install_fakes(monkeypatch, db=NoLookupDb(), settings=_settings())
    with pytest.raises(HTTPException) as exc:
        await people.update_person_im_handle(
            _make_request(role="CLIENT"), uuid.uuid4(), uuid.uuid4(), {"label": "Synthetic"}
        )
    assert exc.value.status_code == 403
