"""In-container smoke for the Brigade verifier: RS256 valid, EdDSA rejected,
multi-issuer legit accepted, cross-broker impersonation blocked. No pytest."""
import base64
import time

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contact_ops.core.config import Settings
from contact_ops.services.brigade_jwt_verifier import (
    BrigadeJWKSCache,
    _verify_brigade_jwt_with_reason,
)


def b64u(d: bytes) -> str:
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


def b64u_uint(n: int) -> str:
    length = (n.bit_length() + 7) // 8 or 1
    return b64u(n.to_bytes(length, "big"))


def rsa_kp(kid: str):
    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = pk.public_key().public_numbers()
    return pk, {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
                "n": b64u_uint(nums.n), "e": b64u_uint(nums.e)}


def install(url_to_body):
    class R:
        def __init__(self, b): self._b = b
        def raise_for_status(self): pass
        def json(self): return self._b

    class C:
        def __init__(self, timeout=None): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url):
            if url not in url_to_body:
                raise httpx.HTTPError("no stub " + url)
            return R(url_to_body[url])

    httpx.Client = C


def tok(priv, kid, s, alg="RS256", **ov):
    now = int(time.time())
    c = {"iss": s.BRIGADE_TRUSTED_ISSUER, "aud": s.BRIGADE_EXPECTED_AUDIENCE,
         "sub": "uc-user-1", "org_id": "00000000-0000-0000-0000-000000000001",
         "iat": now, "nbf": now - 1, "exp": now + 300}
    c.update(ov)
    return jwt.encode(c, priv, algorithm=alg, headers={"kid": kid} if kid else None)


results = []

# 1) single-issuer RS256 valid (the real production path)
s = Settings()
pk, jwk = rsa_kp("k1")
install({f"{s.BRIGADE_TRUSTED_ISSUER}/.well-known/jwks.json": {"keys": [jwk]},
         s.BRIGADE_JWKS_URL: {"keys": [jwk]}})
cache = BrigadeJWKSCache(s); cache.prime()
r = _verify_brigade_jwt_with_reason(tok(pk, "k1", s), settings=s, jwks_cache=cache)
results.append(("RS256 valid", r.valid is True, r.reason))

# 2) EdDSA rejected at the alg gate
ed = Ed25519PrivateKey.generate()
r = _verify_brigade_jwt_with_reason(tok(ed, "k1", s, alg="EdDSA"), settings=s, jwks_cache=cache)
results.append(("EdDSA alg-rejected", r.valid is False and r.reason == "alg-rejected", r.reason))

# 3) multi-issuer: legit second broker + cross-broker impersonation
A, B = "https://broker-a.example", "https://broker-b.example"
sm = Settings(BRIGADE_TRUSTED_ISSUERS=f"{A},{B}", BRIGADE_JWKS_URL="")
pka, jwka = rsa_kp("kid-a")
pkb, jwkb = rsa_kp("kid-b")
install({f"{A}/.well-known/jwks.json": {"keys": [jwka]},
         f"{B}/.well-known/jwks.json": {"keys": [jwkb]}})
cache2 = BrigadeJWKSCache(sm); cache2.prime()
r = _verify_brigade_jwt_with_reason(tok(pkb, "kid-b", sm, iss=B), settings=sm, jwks_cache=cache2)
results.append(("multi legit B valid", r.valid is True, r.reason))
r = _verify_brigade_jwt_with_reason(tok(pka, "kid-a", sm, iss=B), settings=sm, jwks_cache=cache2)
results.append(("cross-broker rejected", r.valid is False and r.reason == "iss-key-mismatch", r.reason))

ok = all(p for _, p, _ in results)
for name, p, reason in results:
    print("PASS" if p else "FAIL", "|", name, "| reason =", reason)
print("ALL_OK" if ok else "SOME_FAILED")
