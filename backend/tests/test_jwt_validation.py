"""
Real JWT validation tests with mocked JWKS.

Generates an RS256 keypair at test setup and tests:
  - Valid signed JWT accepted
  - Expired token rejected
  - Wrong audience rejected
  - Wrong issuer rejected
  - Tampered signature rejected
  - Unsigned (alg:none) token rejected
  - kid not in JWKS rejected
  - Missing tenant_id claim rejected
  - act claim chain preserved
  - No token returns 401 when Keycloak is configured (not silent-bypass)

All tests hit /mcp (NOT /health or / which are in SKIP_PATHS).
"""

import base64
import json as _json
import os
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from contact_ops.core.config import Settings, get_settings
from contact_ops.main import app


# ── helpers ──────────────────────────────────────────────────────────────


def _int_to_b64url(num: int) -> str:
    length = (num.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(num.to_bytes(length, "big")).rstrip(b"=").decode()


@pytest.fixture(scope="session")
def rsa_keypair():
    """Generate an RS256 keypair once per test session."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    pub_numbers = public_key.public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-key-1",
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_b64url(pub_numbers.n),
        "e": _int_to_b64url(pub_numbers.e),
    }
    return {"private_pem": private_pem, "jwk": jwk, "public_key": public_key}


def _jwks_response(jwk: dict) -> dict:
    return {"keys": [jwk]}


def _make_token(claims: dict, key_pem: str, headers: dict | None = None) -> str:
    from jose import jwt as jose_jwt
    if headers is None:
        headers = {"kid": "test-key-1"}
    return jose_jwt.encode(claims, key_pem, algorithm="RS256", headers=headers)


def _tamper_signature(token: str) -> str:
    """Return ``token`` with a genuinely altered RS256 signature.

    NOTE: flipping the *last* base64url char of the signature is unreliable —
    for a 256-byte RSA signature the final char carries only 2 significant bits
    (the other 4 are zero-padding), so ~30% of single-char flips decode back to
    identical bytes and the signature stays valid. We instead decode the
    signature, flip a bit in the FIRST byte (always fully significant), and
    re-encode, so the tamper is deterministic on every run.
    """
    header, payload, signature = token.split(".")
    raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    mutated = bytes([raw[0] ^ 0x01]) + raw[1:]
    new_sig = base64.urlsafe_b64encode(mutated).rstrip(b"=").decode()
    return ".".join([header, payload, new_sig])


VALID_ISSUER = "https://test-keycloak.invalid/realms/uchub"
VALID_JWKS_URL = "https://test-keycloak.invalid/protocol/openid-connect/certs"
VALID_AUD = "contact-ops-mcp"


@pytest.fixture(autouse=True)
def _reset_settings():
    """Reset relevant settings to known state before each test."""
    s = get_settings()
    s.STANDALONE_MODE = False
    s.KEYCLOAK_ISSUER = VALID_ISSUER
    s.KEYCLOAK_JWKS_URL = VALID_JWKS_URL
    s.KEYCLOAK_AUDIENCE = VALID_AUD
    s.JWT_ALLOWED_ALGORITHMS = ["RS256"]
    # These tests exercise the Keycloak validation path; they mock ONLY the
    # Keycloak JWKS endpoint. When a token legitimately fails KC validation
    # (expired / wrong aud / wrong iss / tampered / unknown kid / no tenant_id),
    # dispatch() falls through to the Brigade verifier, which would otherwise
    # httpx.GET the real BRIGADE_JWKS_URL (https://brigade.unicorncommander.ai/...)
    # — an unmocked request respx rejects with AllMockedAssertionError. Blank the
    # Brigade JWKS URL so that fallback fail-closes OFFLINE (the verifier logs
    # "jwks-url-unconfigured" and returns None, no network), which is exactly the
    # 401 these tests assert. This neither weakens validation nor changes Brigade
    # path behavior — production keeps the real URL; only this test env clears it.
    s.BRIGADE_JWKS_URL = ""
    # Reset the verifier's module-level JWKS cache singleton so no primed keys
    # from another test in the session can satisfy a kid lookup here (keeps the
    # Brigade fallback deterministic and offline regardless of test order).
    import contact_ops.services.brigade_jwt_verifier as _bv

    _bv._default_cache = None
    # Clear cached JWKS on the middleware instance
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware
    for mw in app.user_middleware:
        if isinstance(mw, JWTValidationMiddleware):
            mw._jwks = None
            mw._jwks_expires = 0.0


def _mcp_payload(method: str = "initialize", id_val: str = "test-1") -> str:
    return _json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": {"protocolVersion": "2025-11-25"},
        "id": id_val,
    })


def _mcp_post(token: str | None = None) -> TestClient:
    """Helper: POST to /mcp with optional bearer token."""
    headers = {"content-type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return TestClient(app).post("/mcp", data=_mcp_payload(), headers=headers)


# ── tests ─────────────────────────────────────────────────────────────────


def test_valid_signed_jwt_accepted(rsa_keypair):
    """A properly-signed JWT with all required claims is accepted."""
    import respx

    now = int(time.time())
    claims = {
        "sub": "test-user-123",
        "iss": VALID_ISSUER,
        "aud": VALID_AUD,
        "tenant_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "iat": now - 60,
        "exp": now + 300,
        "nbf": now - 60,
    }
    token = _make_token(claims, rsa_keypair["private_pem"])

    with respx.mock(assert_all_called=False) as mock:
        mock.get(VALID_JWKS_URL).respond(json=_jwks_response(rsa_keypair["jwk"]))
        resp = _mcp_post(token)

    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body


def test_expired_token_rejected(rsa_keypair):
    """Token with exp in the past is rejected."""
    import respx

    now = int(time.time())
    claims = {
        "sub": "expired-user",
        "iss": VALID_ISSUER,
        "aud": VALID_AUD,
        "tenant_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "iat": now - 3600,
        "exp": now - 1,
        "nbf": now - 3600,
    }
    token = _make_token(claims, rsa_keypair["private_pem"])

    with respx.mock(assert_all_called=False) as mock:
        mock.get(VALID_JWKS_URL).respond(json=_jwks_response(rsa_keypair["jwk"]))
        resp = _mcp_post(token)

    assert resp.status_code == 401


def test_wrong_audience_rejected(rsa_keypair):
    """Token with wrong aud claim is rejected."""
    import respx

    now = int(time.time())
    claims = {
        "sub": "wrong-aud",
        "iss": VALID_ISSUER,
        "aud": "wrong-client",
        "tenant_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "iat": now - 60,
        "exp": now + 300,
        "nbf": now - 60,
    }
    token = _make_token(claims, rsa_keypair["private_pem"])

    with respx.mock(assert_all_called=False) as mock:
        mock.get(VALID_JWKS_URL).respond(json=_jwks_response(rsa_keypair["jwk"]))
        resp = _mcp_post(token)

    assert resp.status_code == 401


def test_wrong_issuer_rejected(rsa_keypair):
    """Token from a different issuer is rejected."""
    import respx

    now = int(time.time())
    claims = {
        "sub": "wrong-iss",
        "iss": "https://evil.example/realms/uchub",
        "aud": VALID_AUD,
        "tenant_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "iat": now - 60,
        "exp": now + 300,
        "nbf": now - 60,
    }
    token = _make_token(claims, rsa_keypair["private_pem"])

    with respx.mock(assert_all_called=False) as mock:
        mock.get(VALID_JWKS_URL).respond(json=_jwks_response(rsa_keypair["jwk"]))
        resp = _mcp_post(token)

    assert resp.status_code == 401


def test_tampered_signature_rejected(rsa_keypair):
    """Token with a flipped byte in the signature → verification fails."""
    import respx

    now = int(time.time())
    claims = {
        "sub": "test-user",
        "iss": VALID_ISSUER,
        "aud": VALID_AUD,
        "tenant_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "iat": now - 60,
        "exp": now + 300,
        "nbf": now - 60,
    }
    token = _make_token(claims, rsa_keypair["private_pem"])
    tampered = _tamper_signature(token)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(VALID_JWKS_URL).respond(json=_jwks_response(rsa_keypair["jwk"]))
        resp = _mcp_post(tampered)

    assert resp.status_code == 401


def test_unsigned_alg_none_rejected(rsa_keypair):
    """Token with alg:none and no signature is rejected."""
    import respx

    now = int(time.time())
    header = _json.dumps({"alg": "none", "typ": "JWT"})
    payload = _json.dumps({
        "sub": "evil",
        "iss": VALID_ISSUER,
        "aud": VALID_AUD,
        "tenant_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "exp": now + 300,
    })
    token = (
        base64.urlsafe_b64encode(header.encode()).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
        + "."
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(VALID_JWKS_URL).respond(json=_jwks_response(rsa_keypair["jwk"]))
        resp = _mcp_post(token)

    assert resp.status_code == 401


def test_kid_not_in_jwks_rejected(rsa_keypair):
    """Token signed with a kid that is NOT in the published JWKS."""
    import respx

    now = int(time.time())
    claims = {
        "sub": "unknown-kid",
        "iss": VALID_ISSUER,
        "aud": VALID_AUD,
        "tenant_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "iat": now - 60,
        "exp": now + 300,
        "nbf": now - 60,
    }
    token = _make_token(
        claims,
        rsa_keypair["private_pem"],
        headers={"kid": "unknown-key-999"},
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(VALID_JWKS_URL).respond(json=_jwks_response(rsa_keypair["jwk"]))
        resp = _mcp_post(token)

    assert resp.status_code == 401


def test_missing_tenant_id_claim_rejected(rsa_keypair):
    """Token with all standard claims but no tenant_id → rejected."""
    import respx

    now = int(time.time())
    claims = {
        "sub": "no-tenant",
        "iss": VALID_ISSUER,
        "aud": VALID_AUD,
        "iat": now - 60,
        "exp": now + 300,
        "nbf": now - 60,
    }
    token = _make_token(claims, rsa_keypair["private_pem"])

    with respx.mock(assert_all_called=False) as mock:
        mock.get(VALID_JWKS_URL).respond(json=_jwks_response(rsa_keypair["jwk"]))
        resp = _mcp_post(token)

    assert resp.status_code == 401


def test_act_claim_chain_preserved():
    """Token with nested act claim → _build_actor_chain preserves it."""
    from contact_ops.middleware.jwt_validation import _build_actor_chain, _root_subject

    claims = {
        "sub": "agent-1",
        "act": {"sub": "human-user"},
    }
    chain = _build_actor_chain(claims)
    assert chain["sub"] == "agent-1"
    assert chain["act"]["sub"] == "human-user"

    root = _root_subject(claims)
    assert root == "human-user"


def test_no_token_returns_401_not_anonymous():
    """When Keycloak is configured but no Authorization header → 401."""
    # Settings already configured by _reset_settings autouse fixture
    resp = _mcp_post(token=None)
    assert resp.status_code == 401


async def test_kc_uc_uid_pinned_to_sub_ignores_divergent_claim(rsa_keypair):
    """A KC token whose uc_uid claim differs from sub resolves uc_uid to sub.

    Contact-Ops keys membership (user_tenant_membership.user_uc_uid) on the
    Keycloak subject. A stray uc_uid mapper must NOT silently re-key the
    identity, which would 403 every user under the membership gate. The
    divergent claim is ignored (and logged loudly) rather than honored.
    """
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware

    now = int(time.time())
    claims = {
        "sub": "kc-subject-abc",
        "uc_uid": "some-other-id-that-must-be-ignored",
        "iss": VALID_ISSUER,
        "aud": VALID_AUD,
        "tenant_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "iat": now - 60,
        "exp": now + 300,
        "nbf": now - 60,
    }
    token = _make_token(claims, rsa_keypair["private_pem"])

    mw = JWTValidationMiddleware(app, get_settings())
    # Pre-seed the JWKS cache so _validate verifies offline (no network/respx).
    mw._jwks = _jwks_response(rsa_keypair["jwk"])
    mw._jwks_expires = time.time() + 3600
    result = await mw._validate(token)

    assert result is not None
    # Pinned to sub — the divergent uc_uid claim is deliberately discarded.
    assert result["uc_uid"] == "kc-subject-abc"


async def test_kc_uc_uid_falls_back_to_sub_when_absent(rsa_keypair):
    """A KC token with no uc_uid claim resolves uc_uid to sub (unchanged behavior)."""
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware

    now = int(time.time())
    claims = {
        "sub": "kc-subject-xyz",
        "iss": VALID_ISSUER,
        "aud": VALID_AUD,
        "tenant_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "iat": now - 60,
        "exp": now + 300,
        "nbf": now - 60,
    }
    token = _make_token(claims, rsa_keypair["private_pem"])

    mw = JWTValidationMiddleware(app, get_settings())
    # Pre-seed the JWKS cache so _validate verifies offline (no network/respx).
    mw._jwks = _jwks_response(rsa_keypair["jwk"])
    mw._jwks_expires = time.time() + 3600
    result = await mw._validate(token)

    assert result is not None
    assert result["uc_uid"] == "kc-subject-xyz"


# ── Phase 4.4 onboarding allowance ─────────────────────────────────────────
# A valid KC token that LACKS tenant_id must be accepted by _validate ONLY when
# allow_missing_tenant=True (set by dispatch() for POST /api/auth/onboard), and
# even then ONLY the tenant_id-presence check is relaxed — signature, issuer,
# audience, exp are still fully enforced. Default (False) stays fail-closed.


def _no_tenant_claims():
    now = int(time.time())
    return {
        "sub": "kc-new-user",
        "iss": VALID_ISSUER,
        "aud": VALID_AUD,
        # deliberately NO tenant_id (brand-new user before provisioning)
        "preferred_username": "newuser",
        "email": "REMOVED",
        "iat": now - 60,
        "exp": now + 300,
        "nbf": now - 60,
    }


def _mw_offline(rsa_keypair):
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware

    mw = JWTValidationMiddleware(app, get_settings())
    mw._jwks = _jwks_response(rsa_keypair["jwk"])
    mw._jwks_expires = time.time() + 3600
    return mw


async def test_tenantless_kc_token_rejected_by_default(rsa_keypair):
    """Fail-closed: a tenant-less KC token is rejected when the flag is unset."""
    token = _make_token(_no_tenant_claims(), rsa_keypair["private_pem"])
    mw = _mw_offline(rsa_keypair)
    # Default call (every non-onboard path) must still reject missing tenant_id.
    assert await mw._validate(token) is None


async def test_tenantless_kc_token_accepted_with_onboard_allowance(rsa_keypair):
    """A tenant-less KC token is accepted ONLY with allow_missing_tenant=True,
    and uc_uid is still pinned to sub so the handler has a verified identity."""
    token = _make_token(_no_tenant_claims(), rsa_keypair["private_pem"])
    mw = _mw_offline(rsa_keypair)
    result = await mw._validate(token, allow_missing_tenant=True)
    assert result is not None
    assert result.get("tenant_id") in (None, "")  # still no tenant on the claim
    assert result["uc_uid"] == "kc-new-user"  # uc_uid pinned to sub


async def test_onboard_allowance_does_not_relax_signature(rsa_keypair):
    """The allowance relaxes ONLY tenant_id presence: a tampered signature is
    still rejected even with allow_missing_tenant=True."""
    token = _make_token(_no_tenant_claims(), rsa_keypair["private_pem"])
    tampered = _tamper_signature(token)
    mw = _mw_offline(rsa_keypair)
    assert await mw._validate(tampered, allow_missing_tenant=True) is None


async def test_onboard_allowance_does_not_relax_issuer(rsa_keypair):
    """Even with the allowance on, a wrong-issuer token is still rejected."""
    claims = _no_tenant_claims()
    claims["iss"] = "https://evil.example/realms/uchub"
    token = _make_token(claims, rsa_keypair["private_pem"])
    mw = _mw_offline(rsa_keypair)
    assert await mw._validate(token, allow_missing_tenant=True) is None


async def test_onboard_allowance_does_not_relax_audience(rsa_keypair):
    """Even with the allowance on, a wrong-audience token is still rejected."""
    claims = _no_tenant_claims()
    claims["aud"] = "wrong-client"
    token = _make_token(claims, rsa_keypair["private_pem"])
    mw = _mw_offline(rsa_keypair)
    assert await mw._validate(token, allow_missing_tenant=True) is None


async def test_dispatch_allowance_only_for_post_onboard(rsa_keypair, monkeypatch):
    """dispatch() sets allow_missing_tenant ONLY for POST /api/auth/onboard.

    Verifies the narrowness of the allowance at the dispatch layer without a
    DB: we stub _validate to capture the flag and assert call-by-call.
    """
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware, ONBOARD_PATH

    captured: list[tuple[str, str, bool]] = []

    async def _fake_validate(self, token, *, allow_missing_tenant=False):
        # Record (path-independent) what the dispatcher decided, then short-
        # circuit by returning None so call_next is never reached (no DB).
        captured.append((token, "validated", allow_missing_tenant))
        return None

    monkeypatch.setattr(JWTValidationMiddleware, "_validate", _fake_validate)
    # Brigade fallback would run after _validate returns None; stub it to None too.
    monkeypatch.setattr(
        JWTValidationMiddleware, "_validate_brigade",
        lambda self, token: _async_none(),
    )

    client = TestClient(app, raise_server_exceptions=False)

    # POST /api/auth/onboard -> allowance TRUE
    client.post(ONBOARD_PATH, headers={"Authorization": "Bearer dummy.kc.token"})
    # GET /api/auth/onboard -> allowance FALSE (wrong method)
    client.get(ONBOARD_PATH, headers={"Authorization": "Bearer dummy.kc.token"})
    # POST a different path -> allowance FALSE
    client.post("/mcp", headers={"Authorization": "Bearer dummy.kc.token"},
                data=_mcp_payload())

    flags_by_call = [c[2] for c in captured]
    # The POST /onboard call is the only one that should have allowance True.
    assert flags_by_call.count(True) == 1
    assert flags_by_call[0] is True  # first call = POST /onboard
    assert all(f is False for f in flags_by_call[1:])


async def _async_none():
    return None
