"""Brigade federation JWT verifier — exercises the REAL production path.

Brigade signs with RS256 (its live JWKS is RSA) and the verifier decodes with
python-jose, which has no EdDSA support. The previous version of this file signed
EdDSA tokens with PyJWT and asserted they verified — a path python-jose CANNOT
actually validate, so those "green" cases never exercised production. Every token
here is RS256, round-tripped through the same library the verifier uses, plus:
  - an EdDSA token is asserted REJECTED (we pin RS256 only), and
  - the multi-issuer cross-broker impersonation guard (iss-key-mismatch) is
    covered for the first time.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
    key = _rsa_keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    token = _token(settings, key["private_key"], "brigade-key-1")

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is True
    assert result.claims is not None
    assert result.claims["sub"] == "uc-user-1"
    assert result.claims["org_id"] == "00000000-0000-0000-0000-000000000001"


def test_wrong_audience_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _rsa_keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    token = _token(settings, key["private_key"], "brigade-key-1", aud="other-app")

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.claims is None
    assert result.reason == "aud-mismatch"


def test_expired_token_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _rsa_keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    now = int(time.time())
    token = _token(settings, key["private_key"], "brigade-key-1", exp=now - 60, nbf=now - 120)

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "expired"


def test_wrong_signing_key_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    trusted = _rsa_keypair("brigade-key-1")
    wrong = _rsa_keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [trusted["jwk"]])
    token = _token(settings, wrong["private_key"], "brigade-key-1")

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "signature-invalid"


def test_forged_jwt_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _rsa_keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    token = _token(settings, key["private_key"], "brigade-key-1")
    header, payload, signature = token.split(".")
    forged_payload = _b64url(b'{"sub":"attacker"}')
    forged = ".".join([header, forged_payload, signature])

    result = _verify_brigade_jwt_with_reason(forged, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "signature-invalid"


def test_eddsa_algorithm_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """We pin RS256 only. An EdDSA-signed token is refused at the alg gate.

    Tripwire: if anyone re-adds 'EdDSA' to ALLOWED_BRIGADE_ALGORITHMS without
    giving the verifier a decoder that actually supports it (python-jose does
    not), this breaks — forcing the capability to be made real, not advertised.
    """
    ed = Ed25519PrivateKey.generate()
    cache = _cache(settings, monkeypatch, [])
    now = int(time.time())
    claims = {
        "iss": settings.BRIGADE_TRUSTED_ISSUER,
        "aud": settings.BRIGADE_EXPECTED_AUDIENCE,
        "sub": "uc-user-1",
        "org_id": "00000000-0000-0000-0000-000000000001",
        "iat": now,
        "nbf": now - 1,
        "exp": now + 300,
    }
    token = jwt.encode(claims, ed, algorithm="EdDSA", headers={"kid": "brigade-key-1"})

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "alg-rejected"


def test_missing_kid_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _rsa_keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    token = _token(settings, key["private_key"], None)

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "missing-kid"


def test_unknown_user_style_missing_sub_rejected(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _rsa_keypair("brigade-key-1")
    cache = _cache(settings, monkeypatch, [key["jwk"]])
    token = _token(settings, key["private_key"], "brigade-key-1", sub="")

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "missing-sub"


def test_jwks_stale_new_kid_refreshes_and_succeeds(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_key = _rsa_keypair("old-key")
    new_key = _rsa_keypair("new-key")
    _install_jwks_stub(monkeypatch, [{"keys": [old_key["jwk"]]}, {"keys": [new_key["jwk"]]}])
    cache = BrigadeJWKSCache(settings)
    cache.prime()
    token = _token(settings, new_key["private_key"], "new-key")

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is True
    assert result.claims is not None
    assert result.claims["sub"] == "uc-user-1"


# --- multi-issuer trust: a token's signing key must belong to the issuer it
# claims, so trusting two brokers does not let one impersonate the other. ---

_BROKER_A = "https://broker-a.example"
_BROKER_B = "https://broker-b.example"


def _multi_issuer_settings() -> Settings:
    # BRIGADE_JWKS_URL="" disables the single-issuer pin so BOTH brokers resolve
    # to their derived {iss}/.well-known/jwks.json.
    return Settings(BRIGADE_TRUSTED_ISSUERS=f"{_BROKER_A},{_BROKER_B}", BRIGADE_JWKS_URL="")


def test_second_trusted_broker_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _multi_issuer_settings()
    key_a = _rsa_keypair("kid-a")
    key_b = _rsa_keypair("kid-b")
    _install_jwks_map(
        monkeypatch,
        {
            _jwks_url(_BROKER_A): {"keys": [key_a["jwk"]]},
            _jwks_url(_BROKER_B): {"keys": [key_b["jwk"]]},
        },
    )
    cache = BrigadeJWKSCache(settings)
    cache.prime()
    # Broker B signs with its OWN key and claims iss=B — legitimate.
    token = _token(settings, key_b["private_key"], "kid-b", iss=_BROKER_B)

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is True
    assert result.claims is not None
    assert result.claims["iss"] == _BROKER_B


def test_cross_broker_impersonation_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _multi_issuer_settings()
    key_a = _rsa_keypair("kid-a")
    key_b = _rsa_keypair("kid-b")
    _install_jwks_map(
        monkeypatch,
        {
            _jwks_url(_BROKER_A): {"keys": [key_a["jwk"]]},
            _jwks_url(_BROKER_B): {"keys": [key_b["jwk"]]},
        },
    )
    cache = BrigadeJWKSCache(settings)
    cache.prime()
    # Broker A signs with its REAL key + REAL kid but CLAIMS to be broker B.
    # The signature is valid (A's key), the iss is trusted (B), yet the key that
    # signed it belongs to A — must be rejected, not accepted as B.
    token = _token(settings, key_a["private_key"], "kid-a", iss=_BROKER_B)

    result = _verify_brigade_jwt_with_reason(token, settings=settings, jwks_cache=cache)

    assert result.valid is False
    assert result.reason == "iss-key-mismatch"


# --- helpers ---


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
    """Queue-based JWKS stub: successive fetches pop successive bodies (used to
    simulate key rotation for the stale-refresh test)."""
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


def _install_jwks_map(
    monkeypatch: pytest.MonkeyPatch, url_to_body: dict[str, dict[str, Any]]
) -> None:
    """URL-aware JWKS stub: each issuer's JWKS URL returns ONLY its own keys, so
    a key is bound to the issuer it was published under (multi-issuer tests)."""

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
            if url not in url_to_body:
                raise httpx.HTTPError(f"no stubbed JWKS for {url}")
            return _Response(url_to_body[url])

    monkeypatch.setattr(httpx, "Client", _Client)


def _jwks_url(issuer: str) -> str:
    return f"{issuer}/.well-known/jwks.json"


def _rsa_keypair(kid: str) -> dict[str, Any]:
    """RSA-2048 keypair + its public JWK (kty=RSA, RS256) — what Brigade serves."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    return {
        "private_key": private_key,
        "jwk": {
            "kty": "RSA",
            "kid": kid,
            "use": "sig",
            "alg": "RS256",
            "n": _b64url_uint(numbers.n),
            "e": _b64url_uint(numbers.e),
        },
    }


def _token(
    settings: Settings,
    private_key: rsa.RSAPrivateKey,
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
    headers = {"kid": kid} if kid is not None else None
    token = jwt.encode(claims, private_key, algorithm="RS256", headers=headers)
    return str(token)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_uint(value: int) -> str:
    length = (value.bit_length() + 7) // 8 or 1
    return _b64url(value.to_bytes(length, "big"))
