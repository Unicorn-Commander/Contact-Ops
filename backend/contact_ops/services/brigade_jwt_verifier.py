"""Brigade federation JWT verifier.

Accepts short-lived JWTs issued by Brigade for Contact-Ops and returns the
verified claims. All failures are fail-closed and logged without token content.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, cast

import httpx
from jose import jwk, jwt
from jose.exceptions import (
    ExpiredSignatureError,
    JWKError,
    JWTClaimsError,
    JWTError,
)
import structlog

from contact_ops.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

ALLOWED_BRIGADE_ALGORITHMS = ["EdDSA", "RS256"]
BRIGADE_JWKS_TTL_SECONDS = 24 * 60 * 60
BRIGADE_CLOCK_SKEW_SECONDS = 30


@dataclass(frozen=True)
class BrigadeJWTVerificationResult:
    valid: bool
    claims: dict[str, Any] | None
    reason: str | None


class BrigadeJWKSCache:
    """Thread-safe JWKS cache with kid-miss refresh for Brigade key rotation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks: dict[str, Any] | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def prime(self) -> None:
        self.refresh(force=True)

    def resolve_key(self, kid: str) -> dict[str, Any] | None:
        key = self._find(kid)
        if key is not None:
            return key
        self.refresh(force=True)
        return self._find(kid)

    def refresh(self, *, force: bool = False) -> None:
        with self._lock:
            now = time.time()
            if not force and self._jwks is not None and now < self._expires_at:
                return
            jwks_url = self._settings.BRIGADE_JWKS_URL
            if not jwks_url:
                logger.warning("brigade_jwks_fetch_failed", reason="jwks-url-unconfigured")
                return
            try:
                with httpx.Client(timeout=10) as client:
                    response = client.get(jwks_url)
                    response.raise_for_status()
                    jwks = cast(dict[str, Any], response.json())
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "brigade_jwks_fetch_failed",
                    reason="jwks-fetch-failed",
                    error=str(exc),
                )
                return
            self._jwks = jwks
            self._expires_at = now + BRIGADE_JWKS_TTL_SECONDS
            logger.info(
                "brigade_jwks_refreshed",
                keys=len(jwks.get("keys", [])),
                ttl_s=BRIGADE_JWKS_TTL_SECONDS,
            )

    def _find(self, kid: str) -> dict[str, Any] | None:
        now = time.time()
        if self._jwks is None or now >= self._expires_at:
            self.refresh(force=False)
        if self._jwks is None:
            return None
        keys = self._jwks.get("keys", [])
        if not isinstance(keys, list):
            return None
        for key in keys:
            if isinstance(key, dict) and key.get("kid") == kid:
                return cast(dict[str, Any], key)
        return None


_default_cache: BrigadeJWKSCache | None = None
_default_cache_lock = threading.Lock()


def get_brigade_jwks_cache(settings: Settings | None = None) -> BrigadeJWKSCache:
    global _default_cache
    with _default_cache_lock:
        if _default_cache is None:
            _default_cache = BrigadeJWKSCache(settings or get_settings())
        return _default_cache


def prime_brigade_jwks_cache(settings: Settings | None = None) -> None:
    get_brigade_jwks_cache(settings).prime()


def verify_brigade_jwt(token: str) -> dict[str, Any] | None:
    return verify_brigade_jwt_with_reason(token).claims


def verify_brigade_jwt_with_reason(token: str) -> BrigadeJWTVerificationResult:
    settings = get_settings()
    return _verify_brigade_jwt_with_reason(
        token,
        settings=settings,
        jwks_cache=get_brigade_jwks_cache(settings),
    )


def _verify_brigade_jwt_with_reason(
    token: str,
    *,
    settings: Settings,
    jwks_cache: BrigadeJWKSCache,
) -> BrigadeJWTVerificationResult:
    if token.startswith("co_pat_"):
        return _reject("personal-access-token")

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        return _reject("header-unparseable", error=str(exc))

    alg = header.get("alg")
    if alg not in ALLOWED_BRIGADE_ALGORITHMS:
        return _reject("alg-rejected", alg=alg)

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        return _reject("missing-kid")

    key = jwks_cache.resolve_key(kid)
    if key is None:
        return _reject("kid-not-found", kid=kid)

    try:
        # python-jose's jwk.construct takes the JWK dict + algorithm; both
        # EdDSA (ed25519) and RS256 are supported.
        signing_key = jwk.construct(key, algorithm=alg)
    except (JWKError, ValueError, TypeError) as exc:
        return _reject("invalid-jwks-key", kid=kid, error=str(exc))

    try:
        # python-jose's decode collapses specific exceptions (Expired*,
        # Audience*, Issuer*) under ExpiredSignatureError or JWTClaimsError.
        # We catch each and recover the specific reason from the message
        # to preserve the granular log signal.
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=ALLOWED_BRIGADE_ALGORITHMS,
            audience=settings.BRIGADE_EXPECTED_AUDIENCE,
            issuer=settings.BRIGADE_TRUSTED_ISSUER,
            options={
                "require_exp": True,
                "require_nbf": True,
                "require_iat": True,
                "require_iss": True,
                "require_aud": True,
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": True,
                "leeway": BRIGADE_CLOCK_SKEW_SECONDS,
            },
        )
    except ExpiredSignatureError:
        return _reject("expired", kid=kid)
    except JWTClaimsError as exc:
        # python-jose wraps audience/issuer/nbf failures here. Split by the
        # message text so the log signal stays granular.
        msg = str(exc).lower()
        if "audience" in msg:
            return _reject("aud-mismatch", kid=kid)
        if "issuer" in msg:
            return _reject("iss-mismatch", kid=kid)
        if "not yet valid" in msg or "before" in msg:
            return _reject("not-yet-valid", kid=kid)
        return _reject("claims-invalid", kid=kid, error=str(exc))
    except JWTError as exc:
        # Signature failures + any other JWT-level error land here.
        msg = str(exc).lower()
        if "signature" in msg:
            return _reject("signature-invalid", kid=kid)
        return _reject("invalid-token", kid=kid, error=str(exc))

    if claims.get("aud") != settings.BRIGADE_EXPECTED_AUDIENCE:
        return _reject("aud-mismatch", kid=kid)
    if claims.get("iss") != settings.BRIGADE_TRUSTED_ISSUER:
        return _reject("iss-mismatch", kid=kid)
    if not claims.get("sub"):
        return _reject("missing-sub", kid=kid)
    if not claims.get("org_id"):
        return _reject("missing-org-id", kid=kid)

    return BrigadeJWTVerificationResult(valid=True, claims=claims, reason=None)


def _reject(reason: str, **fields: Any) -> BrigadeJWTVerificationResult:
    logger.warning("brigade_jwt_rejected", reason=reason, **fields)
    return BrigadeJWTVerificationResult(valid=False, claims=None, reason=reason)
