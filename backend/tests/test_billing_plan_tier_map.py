"""Pure (no-DB) tests for the plan-tier lifecycle map, webhook auth + dispatch.

These exercise the MONEY-critical decision logic without a database:
  * the Lago plan-code -> tier reverse map and its integrity vs PLAN_CATALOG;
  * the webhook's event -> tier resolution (activation / termination / unknown);
  * the webhook's authentication — RS256 signature verification against Lago's
    public key (default, fail-closed) + the opt-in static-secret path;
  * that Lago customer provisioning delegates to the provider and never raises.

The DB-backed transitions (operator move, webhook flip writing an audit row) live
in ``test_billing_plan_lifecycle.py`` which needs the Postgres testcontainer.
"""

from __future__ import annotations

import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt

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


@pytest.fixture(scope="module")
def lago_keypair():
    """An RS256 keypair standing in for Lago's webhook signing key.

    Returns the private PEM (Lago signs deliveries with it) and the public PEM
    (Contact-Ops verifies with it via LAGO_WEBHOOK_PUBLIC_KEY).
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return {"private_pem": private_pem, "public_pem": public_pem}


def _sign_lago_jwt(private_pem: str, body: dict | None = None) -> str:
    """Sign a webhook body the way real Lago does (RS256, ``data`` claim)."""
    import json as _json

    return jose_jwt.encode(
        {"data": _json.dumps(body or {}), "iss": "http://lago"},
        private_pem,
        algorithm="RS256",
    )


def _fake_settings(
    *,
    static_secret: str = "",
    public_key: str = "",
    api_url: str = "",
):
    """Return a stand-in Settings exposing the Lago webhook auth knobs.

    ``static_secret`` populates LAGO_WEBHOOK_STATIC_SECRET (opt-in proxy path);
    ``public_key`` populates LAGO_WEBHOOK_PUBLIC_KEY (default JWT path); ``api_url``
    populates LAGO_API_URL (fetch path — unused in these no-network tests).
    """

    class _S:
        LAGO_WEBHOOK_STATIC_SECRET = ""
        LAGO_WEBHOOK_PUBLIC_KEY = ""
        LAGO_API_URL = ""

    _S.LAGO_WEBHOOK_STATIC_SECRET = static_secret
    _S.LAGO_WEBHOOK_PUBLIC_KEY = public_key
    _S.LAGO_API_URL = api_url
    return _S()


# --- default path: RS256 JWT signature verification -------------------------


def test_authenticate_jwt_accepts_valid_lago_signature(lago_keypair):
    from contact_ops.api.webhooks import _authenticate

    body = {"webhook_type": "subscription.created"}
    token = _sign_lago_jwt(lago_keypair["private_pem"], body)
    settings = _fake_settings(public_key=lago_keypair["public_pem"])

    # A genuine Lago-signed delivery must NOT raise.
    _authenticate(_FakeRequest({"X-Lago-Signature": token}, body), settings)


def test_authenticate_jwt_accepts_base64_encoded_public_key(lago_keypair):
    """Lago's /webhooks/public_key returns a base64-encoded PEM — accept it."""
    import base64

    from contact_ops.api.webhooks import _authenticate

    token = _sign_lago_jwt(lago_keypair["private_pem"], {})
    b64_pem = base64.b64encode(lago_keypair["public_pem"].encode()).decode()
    settings = _fake_settings(public_key=b64_pem)

    _authenticate(_FakeRequest({"X-Lago-Signature": token}, {}), settings)


def test_authenticate_jwt_rejects_signature_from_wrong_key(lago_keypair):
    from fastapi import HTTPException

    from contact_ops.api.webhooks import _authenticate

    # Token signed by a DIFFERENT (attacker) private key.
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker_pem = attacker.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    forged = _sign_lago_jwt(attacker_pem, {"webhook_type": "subscription.created"})
    settings = _fake_settings(public_key=lago_keypair["public_pem"])

    with pytest.raises(HTTPException) as ei:
        _authenticate(_FakeRequest({"X-Lago-Signature": forged}, {}), settings)
    assert ei.value.status_code == 401


def test_authenticate_jwt_rejects_garbage_and_missing_signature(lago_keypair):
    from fastapi import HTTPException

    from contact_ops.api.webhooks import _authenticate

    settings = _fake_settings(public_key=lago_keypair["public_pem"])

    with pytest.raises(HTTPException) as ei:
        _authenticate(_FakeRequest({"X-Lago-Signature": "not-a-jwt"}, {}), settings)
    assert ei.value.status_code == 401

    with pytest.raises(HTTPException) as ei2:
        _authenticate(_FakeRequest({}, {}), settings)
    assert ei2.value.status_code == 401


def test_authenticate_jwt_rejects_alg_none(lago_keypair):
    """An unsigned (alg:none) token must never validate against the public key."""
    import base64 as _b64
    import json as _json

    from fastapi import HTTPException

    from contact_ops.api.webhooks import _authenticate

    def _seg(obj: dict) -> str:
        return _b64.urlsafe_b64encode(_json.dumps(obj).encode()).rstrip(b"=").decode()

    # jose refuses to *encode* alg:none, so craft the token by hand: our verifier
    # must reject it at the alg allow-list before any signature check.
    unsigned = f"{_seg({'alg': 'none', 'typ': 'JWT'})}.{_seg({'data': '{}'})}."
    settings = _fake_settings(public_key=lago_keypair["public_pem"])

    with pytest.raises(HTTPException) as ei:
        _authenticate(_FakeRequest({"X-Lago-Signature": unsigned}, {}), settings)
    assert ei.value.status_code == 401


def test_authenticate_unconfigured_fails_closed():
    """No public key AND no static secret => endpoint disabled (503)."""
    from fastapi import HTTPException

    from contact_ops.api.webhooks import _authenticate

    with pytest.raises(HTTPException) as ei:
        _authenticate(_FakeRequest({"X-Lago-Signature": "x"}, {}), _fake_settings())
    assert ei.value.status_code == 503


# --- opt-in static-secret path (proxy-injected deployments) -----------------


def test_authenticate_static_accepts_matching_signature_header():
    from contact_ops.api.webhooks import _authenticate

    req = _FakeRequest({"X-Lago-Signature": _TEST_WH_SECRET}, {})
    # Should not raise.
    _authenticate(req, _fake_settings(static_secret=_TEST_WH_SECRET))


def test_authenticate_static_accepts_matching_bearer():
    from contact_ops.api.webhooks import _authenticate

    req = _FakeRequest({"Authorization": f"Bearer {_TEST_WH_SECRET}"}, {})
    _authenticate(req, _fake_settings(static_secret=_TEST_WH_SECRET))


def test_authenticate_static_rejects_wrong_or_missing_secret():
    from fastapi import HTTPException

    from contact_ops.api.webhooks import _authenticate

    settings = _fake_settings(static_secret=_TEST_WH_SECRET)
    with pytest.raises(HTTPException) as ei:
        _authenticate(_FakeRequest({"X-Lago-Signature": "nope"}, {}), settings)
    assert ei.value.status_code == 401

    with pytest.raises(HTTPException) as ei2:
        _authenticate(_FakeRequest({}, {}), settings)
    assert ei2.value.status_code == 401


# --- full webhook handler (set_tenant_plan_tier monkeypatched) --------------


@pytest.mark.asyncio
async def test_webhook_unconfigured_secret_fails_closed(monkeypatch):
    import contact_ops.api.webhooks as wh

    monkeypatch.setattr(wh, "get_settings", lambda: _fake_settings())
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

    monkeypatch.setattr(wh, "get_settings", lambda: _fake_settings(static_secret=_TEST_WH_SECRET))
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

    monkeypatch.setattr(wh, "get_settings", lambda: _fake_settings(static_secret=_TEST_WH_SECRET))
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

    monkeypatch.setattr(wh, "get_settings", lambda: _fake_settings(static_secret=_TEST_WH_SECRET))
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
