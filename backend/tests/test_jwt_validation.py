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

    # Replace the entire last character of the signature
    parts = token.split(".")
    sig = list(parts[2])
    sig[-1] = "A" if sig[-1] != "A" else "B"
    parts[2] = "".join(sig)
    tampered = ".".join(parts)

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
