from __future__ import annotations

import base64
import time
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from jose import jwt

from contact_ops.core.config import Settings
from contact_ops.services.brigade_jwt_verifier import (
    BrigadeJWKSCache,
    _verify_brigade_jwt_with_reason,
)


@pytest.fixture
def settings() -> Settings:
    return Settings()


def test_valid_brigade_jwt_for_existing_user_resolves(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    token = _token(settings, key["private_key"], "brigade-key-1")

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is True
    assert result.claims is not None
    assert result.claims["sub"] == "uc-user-1"
    assert result.claims["org_id"] == "00000000-0000-0000-0000-000000000001"


def test_wrong_audience_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    token = _token(settings, key["private_key"], "brigade-key-1", aud="other-app")

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.claims is None
    assert result.reason == "aud-mismatch"


def test_expired_token_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    now = int(time.time())
    token = _token(settings, key["private_key"], "brigade-key-1", exp=now - 60, nbf=now - 120)

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "expired"


def test_wrong_signing_key_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    trusted = _keypair("brigade-key-1")
    wrong = _keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [trusted["jwk"]])
    token = _token(settings, wrong["private_key"], "brigade-key-1")

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "signature-invalid"


def test_forged_jwt_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    token = _token(settings, key["private_key"], "brigade-key-1")
    header, payload, signature = token.split(".")
    forged_payload = _b64url(b'{"sub":"attacker"}')
    forged = ".".join([header, forged_payload, signature])

    result = _verify_brigade_jwt_with_reason(forged, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "signature-invalid"


def test_missing_kid_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    token = _token(settings, key["private_key"], None)

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "missing-kid"


def test_unknown_user_style_missing_sub_rejected(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    token = _token(settings, key["private_key"], "brigade-key-1", sub="")

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "missing-sub"


def test_jwks_stale_new_kid_refreshes_and_succeeds(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_key = _keypair("old-key")
    new_key = _keypair("new-key")
    _install_jwks_stub(monkeypatch, [{"keys": [old_key["jwk"]]}, {"keys": [new_key["jwk"]]}])
    cache = BrigadeJWKSCache(settings)
    cache.prime()
    token = _token(settings, new_key["private_key"], "new-key")

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is True
    assert result.claims is not None
    assert result.claims["sub"] == "uc-user-1"


def _cache(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    keys: list[dict[str, Any]],
) -> BrigadeJWKSCache:
    _install_jwks_stub(monkeypatch, [{"keys": keys}])
    cache = BrigadeJWKSCache(settings)
    cache.prime()
    return cache


def _install_jwks_stub(monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, Any]]) -> None:
    queue = list(responses)

    class _Response:
        def __init__(self, body: dict[str, Any]) -> None:
            self._body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._body

    class _Client:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _Response:
            assert url
            if not queue:
                raise httpx.HTTPError("no stubbed JWKS response left")
            return _Response(queue.pop(0))

    monkeypatch.setattr(httpx, "Client", _Client)


def _keypair(kid: str) -> dict[str, Any]:
    # Brigade signs with RS256 in production (ALLOWED_BRIGADE_ALGORITHMS lists
    # EdDSA too, but python-jose — the verifier's crypto backend — has no EdDSA
    # support, so the live path is RS256). Generate an RSA keypair + the RSA
    # JWK the verifier resolves from the JWKS.
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_numbers = private_key.public_key().public_numbers()
    return {
        "private_key": private_key,
        "private_pem": private_pem,
        "jwk": {
            "kty": "RSA",
            "kid": kid,
            "use": "sig",
            "alg": "RS256",
            "n": _int_to_b64url(public_numbers.n),
            "e": _int_to_b64url(public_numbers.e),
        },
    }


def _token(
    settings: Settings,
    private_key: RSAPrivateKey,
    kid: str | None,
    **overrides: Any,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": settings.BRIGADE_TRUSTED_ISSUER,
        "aud": settings.BRIGADE_EXPECTED_AUDIENCE,
        "sub": "uc-user-1",
        "org_id": "00000000-0000-0000-0000-000000000001",
        "scopes": ["read:contacts", "write:contacts"],
        "iat": now,
        "nbf": now - 1,
        "exp": now + 300,
    }
    claims.update(overrides)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    headers = {"kid": kid} if kid is not None else None
    token = jwt.encode(claims, private_pem, algorithm="RS256", headers=headers)
    return str(token)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_to_b64url(num: int) -> str:
    length = (num.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(num.to_bytes(length, "big")).rstrip(b"=").decode("ascii")
