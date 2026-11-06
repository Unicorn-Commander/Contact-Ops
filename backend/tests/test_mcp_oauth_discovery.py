"""MCP OAuth discovery and bearer-edge contract tests."""

from __future__ import annotations

import base64
import time
from contextlib import asynccontextmanager
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from contact_ops.core.config import get_settings
from contact_ops.main import create_application
from contact_ops.mcp import server
from contact_ops.middleware import jwt_validation

ISSUER = "https://auth.example.com/realms/uchub"
JWKS_URL = "https://auth.example.com/realms/uchub/protocol/openid-connect/certs"
BRIGADE_JWKS_URL = "https://brigade.unicorncommander.ai/.well-known/jwks.json"
CANONICAL_MCP_URL = "https://mcp.example.com/mcp"
TENANT_ID = "11111111-1111-4111-8111-111111111111"


def _int_to_b64url(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def rsa_keypair() -> dict[str, Any]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    numbers = public_key.public_numbers()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return {
        "private_pem": private_pem,
        "jwk": {
            "kty": "RSA",
            "kid": "fictional-test-key",
            "use": "sig",
            "alg": "RS256",
            "n": _int_to_b64url(numbers.n),
            "e": _int_to_b64url(numbers.e),
        },
    }


def _token(private_pem: str, *, issuer: str = ISSUER) -> str:
    from jose import jwt

    now = int(time.time())
    return jwt.encode(
        {
            "sub": "fictional-user-555-0101",
            "iss": issuer,
            "aud": "contact-ops-mcp",
            "tenant_id": TENANT_ID,
            "iat": now - 30,
            "exp": now + 300,
            "nbf": now - 30,
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "fictional-test-key"},
    )


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar(self) -> Any:
        return self.value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _MembershipSession:
    def __init__(self, member: bool) -> None:
        self.member = member

    async def execute(self, statement: Any, params: Any = None) -> _ScalarResult:
        del params
        sql = str(statement)
        if "user_is_active_member" in sql:
            return _ScalarResult(self.member)
        if "SELECT role" in sql:
            return _ScalarResult("member" if self.member else None)
        return _ScalarResult(None)

    async def rollback(self) -> None:
        return None


def _session_maker(member: bool):
    @asynccontextmanager
    async def manager():
        yield _MembershipSession(member)

    return manager


async def _noop_bind(*_args: Any, **_kwargs: Any) -> None:
    return None


def _patch_settings(monkeypatch: pytest.MonkeyPatch, *, member: bool) -> None:
    settings = get_settings()
    settings.STANDALONE_MODE = False
    settings.ENV = "test"
    settings.KEYCLOAK_ISSUER = ISSUER
    settings.KEYCLOAK_JWKS_URL = JWKS_URL
    settings.KEYCLOAK_AUDIENCE = "contact-ops-mcp"
    settings.MCP_CANONICAL_URL = CANONICAL_MCP_URL
    settings.MEMBERSHIP_GATE_ENFORCED = True
    settings.BRIGADE_JWKS_URL = BRIGADE_JWKS_URL

    maker = _session_maker(member)
    monkeypatch.setattr(jwt_validation, "async_session_maker", maker)
    monkeypatch.setattr(server, "async_session_maker", maker)
    monkeypatch.setattr(jwt_validation, "bind_session_context", _noop_bind)
    monkeypatch.setattr(server, "bind_session_context", _noop_bind)
    monkeypatch.setattr(jwt_validation, "verify_brigade_jwt", lambda _token: None)

    async def membership(*_args: Any, **_kwargs: Any) -> bool:
        return member

    monkeypatch.setattr(jwt_validation, "_user_is_active_member", membership)
    monkeypatch.setattr("contact_ops.core.database._user_is_active_member", membership)


@pytest.fixture
def configured_app(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    previous = {
        "STANDALONE_MODE": settings.STANDALONE_MODE,
        "ENV": settings.ENV,
        "KEYCLOAK_ISSUER": settings.KEYCLOAK_ISSUER,
        "KEYCLOAK_JWKS_URL": settings.KEYCLOAK_JWKS_URL,
        "KEYCLOAK_AUDIENCE": settings.KEYCLOAK_AUDIENCE,
        "MCP_CANONICAL_URL": settings.MCP_CANONICAL_URL,
        "MEMBERSHIP_GATE_ENFORCED": settings.MEMBERSHIP_GATE_ENFORCED,
        "BRIGADE_JWKS_URL": settings.BRIGADE_JWKS_URL,
    }
    _patch_settings(monkeypatch, member=True)
    application = create_application()
    yield application
    for name, value in previous.items():
        setattr(settings, name, value)


def _serve_jwks(monkeypatch: pytest.MonkeyPatch, jwk: dict[str, Any] | None) -> None:
    async def refresh(middleware: jwt_validation.JWTValidationMiddleware) -> None:
        middleware._jwks = {"keys": [jwk] if jwk is not None else []}
        middleware._jwks_expires = time.time() + 300

    monkeypatch.setattr(jwt_validation.JWTValidationMiddleware, "_refresh_jwks", refresh)


def _tools_list_payload() -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": "tools/list", "id": "fictional-list-1"}


def test_prm_is_public_and_matches_the_mcp_resource(configured_app):
    client = TestClient(configured_app, raise_server_exceptions=False)

    responses = [
        client.get("/.well-known/oauth-protected-resource"),
        client.get("/.well-known/oauth-protected-resource/mcp"),
    ]

    for response in responses:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["access-control-allow-origin"] == "*"
        assert response.headers["cache-control"] == "public, max-age=3600"
        assert response.json()["resource"] == CANONICAL_MCP_URL
        assert response.json()["authorization_servers"] == [ISSUER]
        assert response.json()["scopes_supported"] == [
            "openid",
            "profile",
            "email",
            "offline_access",
            "mcp",
        ]

    assert responses[0].json() == responses[1].json()


def test_prm_options_and_mcp_preflight_are_cors_safe(configured_app):
    client = TestClient(configured_app, raise_server_exceptions=False)
    prm_options = client.options(
        "/.well-known/oauth-protected-resource",
        headers={"Origin": "https://hosted-client.example"},
    )
    preflight = client.options(
        "/mcp",
        headers={
            "Origin": "https://mcp.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    rejected = client.options(
        "/mcp",
        headers={"Origin": "https://blocked.example"},
    )

    assert prm_options.status_code == 200
    assert prm_options.headers["access-control-allow-origin"] == "*"
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "https://mcp.example.com"
    assert "Authorization" in preflight.headers["access-control-allow-headers"]
    assert preflight.headers["access-control-expose-headers"] == "Mcp-Session-Id"
    assert rejected.status_code == 403
    assert "www-authenticate" not in rejected.headers


def test_mcp_401_challenge_names_prm_and_does_not_reflect_a_token(
    configured_app, monkeypatch: pytest.MonkeyPatch
):
    _serve_jwks(monkeypatch, None)
    client = TestClient(configured_app, raise_server_exceptions=False)
    missing = client.post("/mcp", json={})
    rejected = client.post(
        "/mcp",
        headers={"Authorization": "Bearer fictional-invalid-token"},
        json={},
    )

    challenge = missing.headers["www-authenticate"]
    assert missing.status_code == 401
    assert challenge == (
        'Bearer realm="contact-ops-mcp", '
        'resource_metadata="https://mcp.example.com/.well-known/'
        'oauth-protected-resource/mcp"'
    )
    assert 'error="invalid_token"' not in challenge
    assert rejected.status_code == 401
    assert 'error="invalid_token"' in rejected.headers["www-authenticate"]
    assert "fictional-invalid-token" not in rejected.text


def test_valid_uchub_token_and_membership_can_list_tools(
    configured_app, monkeypatch: pytest.MonkeyPatch, rsa_keypair
):
    _patch_settings(monkeypatch, member=True)
    _serve_jwks(monkeypatch, rsa_keypair["jwk"])
    token = _token(rsa_keypair["private_pem"])
    response = TestClient(configured_app).post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json=_tools_list_payload(),
    )

    assert response.status_code == 200
    assert response.json()["result"]["tools"]


def test_valid_uchub_token_without_membership_is_jsonrpc_403(
    configured_app, monkeypatch: pytest.MonkeyPatch, rsa_keypair
):
    _patch_settings(monkeypatch, member=False)
    _serve_jwks(monkeypatch, rsa_keypair["jwk"])
    token = _token(rsa_keypair["private_pem"])
    response = TestClient(configured_app).post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json=_tools_list_payload(),
    )

    assert response.status_code == 403
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["error"]["code"] == -32003
    assert "not a member" in body["error"]["message"]


def test_wrong_issuer_is_401_with_discovery_challenge(
    configured_app, monkeypatch: pytest.MonkeyPatch, rsa_keypair
):
    _patch_settings(monkeypatch, member=True)
    _serve_jwks(monkeypatch, rsa_keypair["jwk"])
    token = _token(rsa_keypair["private_pem"], issuer="https://evil.example/realms/uchub")
    response = TestClient(configured_app).post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json=_tools_list_payload(),
    )

    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["www-authenticate"]


def test_pat_claims_keep_the_existing_mcp_path(
    monkeypatch: pytest.MonkeyPatch, configured_app
):
    settings = get_settings()
    settings.MEMBERSHIP_GATE_ENFORCED = True

    async def fake_validate_pat(_middleware, _token):
        return (
            {
                "sub": "fictional-pat-user-555-0102",
                "uc_uid": "fictional-pat-user-555-0102",
                "iss": "contact-ops:personal-access-token",
                "tenant_id": TENANT_ID,
            },
            None,
            None,
        )

    monkeypatch.setattr(
        jwt_validation.JWTValidationMiddleware,
        "_validate_pat",
        fake_validate_pat,
    )
    response = TestClient(configured_app).post(
        "/mcp",
        headers={"Authorization": "Bearer co_pat_fictional-fixture"},
        json=_tools_list_payload(),
    )

    assert response.status_code == 200
    assert response.json()["result"]["tools"]
