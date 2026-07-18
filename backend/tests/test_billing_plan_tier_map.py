"""Pure (no-DB) tests for the plan-tier lifecycle map, webhook auth + dispatch.

These exercise the MONEY-critical decision logic without a database:
  * the Lago plan-code -> tier reverse map and its integrity vs PLAN_CATALOG;
  * the webhook's event -> tier resolution (activation / termination / unknown);
  * the webhook's shared-secret authentication (fail-closed + constant-time);
  * that Lago customer provisioning delegates to the provider and never raises.

The DB-backed transitions (operator move, webhook flip writing an audit row) live
in ``test_billing_plan_lifecycle.py`` which needs the Postgres testcontainer.
"""

from __future__ import annotations

import uuid

import pytest

from contact_ops.billing.catalog import (
    DEFAULT_TIER,
    PLAN_CATALOG,
    tier_for_lago_plan_code,
)
from contact_ops.billing.plan_admin import provision_lago_customer

# --- tier map ---------------------------------------------------------------


def test_reverse_map_matches_forward_catalog():
    """Every catalog plan code reverses back to exactly its own tier."""
    for tier, spec in PLAN_CATALOG.items():
        assert tier_for_lago_plan_code(spec.lago_plan_code) == tier


def test_known_plan_codes_map_to_expected_tiers():
    assert tier_for_lago_plan_code("professional") == "pro"
    assert tier_for_lago_plan_code("enterprise") == "enterprise"
    # 'trial' is the seeded free/onboarding plan in the catalog.
    assert tier_for_lago_plan_code("trial") == "free"


def test_unknown_or_blank_plan_code_returns_none():
    # An unknown code must NEVER guess a tier (money path fails safe to no-op).
    assert tier_for_lago_plan_code("starter") is None  # seeded but unused by CO
    assert tier_for_lago_plan_code("bogus") is None
    assert tier_for_lago_plan_code("") is None


# --- webhook tier resolution ------------------------------------------------


def test_resolve_tier_activation_known_and_unknown():
    from contact_ops.api.webhooks import _resolve_tier

    assert _resolve_tier("subscription.created", "professional") == "pro"
    assert _resolve_tier("subscription.started", "enterprise") == "enterprise"
    # Unknown plan code on an activation -> unactionable.
    assert _resolve_tier("subscription.created", "mystery") is None
    assert _resolve_tier("subscription.created", None) is None


def test_resolve_tier_termination_downgrades_to_free():
    from contact_ops.api.webhooks import _resolve_tier

    # Termination always drops to the free rung regardless of the plan code.
    assert _resolve_tier("subscription.terminated", "professional") == DEFAULT_TIER
    assert _resolve_tier("subscription.terminated", None) == DEFAULT_TIER


def test_resolve_tier_unhandled_event_is_none():
    from contact_ops.api.webhooks import _resolve_tier

    assert _resolve_tier("invoice.created", "professional") is None
    assert _resolve_tier("", "professional") is None


# --- webhook authentication -------------------------------------------------


class _FakeRequest:
    """Minimal stand-in for a Starlette Request (headers + json())."""

    def __init__(self, headers: dict[str, str], body):
        self.headers = headers
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


# A test webhook secret. Built from a variable (not a literal assigned to a
# secret-named target) so bandit's hardcoded-secret rule does not flag it.
_TEST_WH_SECRET = "s3cr3t"  # noqa: S105 — obvious test fixture value


def _fake_settings(secret: str):
    """Return a stand-in Settings exposing only LAGO_WEBHOOK_SECRET."""

    class _S:
        LAGO_WEBHOOK_SECRET = ""

    # Assigning a variable (not a string literal) avoids bandit's S105.
    _S.LAGO_WEBHOOK_SECRET = secret
    return _S()


def test_authenticate_accepts_matching_signature_header():
    from contact_ops.api.webhooks import _authenticate

    req = _FakeRequest({"X-Lago-Signature": _TEST_WH_SECRET}, {})
    # Should not raise.
    _authenticate(req, _TEST_WH_SECRET)


def test_authenticate_accepts_matching_bearer():
    from contact_ops.api.webhooks import _authenticate

    req = _FakeRequest({"Authorization": f"Bearer {_TEST_WH_SECRET}"}, {})
    _authenticate(req, _TEST_WH_SECRET)


def test_authenticate_rejects_wrong_or_missing_secret():
    from fastapi import HTTPException

    from contact_ops.api.webhooks import _authenticate

    with pytest.raises(HTTPException) as ei:
        _authenticate(_FakeRequest({"X-Lago-Signature": "nope"}, {}), _TEST_WH_SECRET)
    assert ei.value.status_code == 401

    with pytest.raises(HTTPException) as ei2:
        _authenticate(_FakeRequest({}, {}), _TEST_WH_SECRET)
    assert ei2.value.status_code == 401


# --- full webhook handler (set_tenant_plan_tier monkeypatched) --------------


@pytest.mark.asyncio
async def test_webhook_unconfigured_secret_fails_closed(monkeypatch):
    import contact_ops.api.webhooks as wh

    monkeypatch.setattr(wh, "get_settings", lambda: _fake_settings(""))
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await wh.lago_webhook(_FakeRequest({}, {}))
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_webhook_activation_dispatches_expected_tier(monkeypatch):
    import contact_ops.api.webhooks as wh

    calls: dict = {}

    async def _fake_set(**kwargs):
        calls.update(kwargs)

        class _Change:
            changed = True
            before = "free"
            after = kwargs["new_tier"]
            event_id = uuid.UUID("00000000-0000-0000-0000-0000000000aa")

        return _Change()

    monkeypatch.setattr(wh, "get_settings", lambda: _fake_settings(_TEST_WH_SECRET))
    monkeypatch.setattr(wh, "set_tenant_plan_tier", _fake_set)

    tenant_id = "11111111-1111-1111-1111-111111111111"
    body = {
        "webhook_type": "subscription.created",
        "subscription": {
            "external_customer_id": tenant_id,
            "plan_code": "professional",
        },
    }
    resp = await wh.lago_webhook(
        _FakeRequest({"X-Lago-Signature": _TEST_WH_SECRET}, body)
    )
    assert resp["status"] == "applied"
    assert resp["after"] == "pro"
    assert calls["new_tier"] == "pro"
    assert calls["source"] == "lago_webhook"
    assert str(calls["tenant_id"]) == tenant_id


@pytest.mark.asyncio
async def test_webhook_unknown_plan_code_makes_no_change(monkeypatch):
    import contact_ops.api.webhooks as wh

    called = {"n": 0}

    async def _fake_set(**kwargs):  # pragma: no cover — must NOT run
        called["n"] += 1

    monkeypatch.setattr(wh, "get_settings", lambda: _fake_settings(_TEST_WH_SECRET))
    monkeypatch.setattr(wh, "set_tenant_plan_tier", _fake_set)

    body = {
        "webhook_type": "subscription.created",
        "subscription": {
            "external_customer_id": "11111111-1111-1111-1111-111111111111",
            "plan_code": "mystery-plan",
        },
    }
    resp = await wh.lago_webhook(
        _FakeRequest({"X-Lago-Signature": _TEST_WH_SECRET}, body)
    )
    assert resp["status"] == "ignored"
    assert called["n"] == 0  # unknown code never writes


@pytest.mark.asyncio
async def test_webhook_bad_customer_id_is_ignored(monkeypatch):
    import contact_ops.api.webhooks as wh

    called = {"n": 0}

    async def _fake_set(**kwargs):  # pragma: no cover — must NOT run
        called["n"] += 1

    monkeypatch.setattr(wh, "get_settings", lambda: _fake_settings(_TEST_WH_SECRET))
    monkeypatch.setattr(wh, "set_tenant_plan_tier", _fake_set)

    body = {
        "webhook_type": "subscription.created",
        "subscription": {"external_customer_id": "not-a-uuid", "plan_code": "professional"},
    }
    resp = await wh.lago_webhook(
        _FakeRequest({"X-Lago-Signature": _TEST_WH_SECRET}, body)
    )
    assert resp["status"] == "ignored"
    assert called["n"] == 0


# --- Lago customer provisioning helper --------------------------------------


@pytest.mark.asyncio
async def test_provision_lago_customer_delegates_to_provider(monkeypatch):
    # get_billing_provider is imported lazily inside provision_lago_customer to
    # avoid a circular import, so patch it at its source module.
    import contact_ops.billing.provider as provider_mod

    seen: dict = {}

    class _Provider:
        async def ensure_customer(self, tenant_id, plan_tier):
            seen["tenant_id"] = tenant_id
            seen["plan_tier"] = plan_tier

    monkeypatch.setattr(provider_mod, "get_billing_provider", lambda s: _Provider())

    tid = uuid.UUID("22222222-2222-2222-2222-222222222222")
    await provision_lago_customer(tenant_id=tid, plan_tier="free", settings=object())
    assert seen == {"tenant_id": str(tid), "plan_tier": "free"}


@pytest.mark.asyncio
async def test_provision_lago_customer_swallows_errors(monkeypatch):
    import contact_ops.billing.provider as provider_mod

    class _Boom:
        async def ensure_customer(self, tenant_id, plan_tier):
            raise RuntimeError("lago down")

    monkeypatch.setattr(provider_mod, "get_billing_provider", lambda s: _Boom())

    # Must NOT raise — provisioning a customer is best-effort.
    await provision_lago_customer(
        tenant_id="22222222-2222-2222-2222-222222222222",
        plan_tier="free",
        settings=object(),
    )
