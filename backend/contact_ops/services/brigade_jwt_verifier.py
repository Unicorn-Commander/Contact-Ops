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

# Brigade signs with RS256 (its live JWKS is RSA). We pin RS256 ONLY: the
# pinned python-jose has no EdDSA/OKP support, so listing "EdDSA" would advertise
# a capability we cannot actually verify (an EdDSA token fails closed at key
# construction, never validates) — a latent lie, and a needless JWT
# alg-confusion surface. Keeping the allow-list to exactly the algorithm in use
# is the defensive posture. If Brigade ever migrates to EdDSA, switch THIS
# module's decode path to PyJWT (already a dependency — it does EdDSA) and add a
# real EdDSA round-trip test; python-jose will not gain EdDSA support.
ALLOWED_BRIGADE_ALGORITHMS = ["RS256"]
BRIGADE_JWKS_TTL_SECONDS = 24 * 60 * 60
BRIGADE_CLOCK_SKEW_SECONDS = 30


def _trusted_issuers(settings: Settings) -> list[str]:
    """The Brigade issuers Contact-Ops accepts. BRIGADE_TRUSTED_ISSUERS (CSV),
    when set, trusts multiple brokers at once (e.g. customer + dogfood) during or
    after a sovereign-federation migration; otherwise the singular
    BRIGADE_TRUSTED_ISSUER is used. Trailing slashes are normalized so the `iss`
    claim comparison is exact."""
    raw = (getattr(settings, "BRIGADE_TRUSTED_ISSUERS", "") or "").strip()
    if raw:
        issuers = [i.strip().rstrip("/") for i in raw.split(",") if i.strip()]
        if issuers:
            return issuers
    return [settings.BRIGADE_TRUSTED_ISSUER.rstrip("/")]


def _issuer_jwks_urls(settings: Settings) -> dict[str, str]:
    """issuer -> JWKS URL for every trusted issuer. Derives
    {issuer}/.well-known/jwks.json; the explicit BRIGADE_JWKS_URL still pins the
    primary (singular) issuer's JWKS when set, for back-compat."""
    urls: dict[str, str] = {}
    for iss in _trusted_issuers(settings):
        urls[iss] = f"{iss}/.well-known/jwks.json"
    primary = settings.BRIGADE_TRUSTED_ISSUER.rstrip("/")
    pin = (getattr(settings, "BRIGADE_JWKS_URL", "") or "").strip()
    if pin and primary in urls:
        urls[primary] = pin
    return urls


@dataclass(frozen=True)
class BrigadeJWTVerificationResult:
    valid: bool
    claims: dict[str, Any] | None
    reason: str | None


class BrigadeJWKSCache:
    """Thread-safe JWKS cache with kid-miss refresh for Brigade key rotation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # kid -> (issuer, JWK). Binding each key to the issuer whose JWKS it came
        # from lets the verifier require that a token's signing key belongs to
        # the issuer the token CLAIMS — so trusting brokers A and B does not let
        # A mint a token claiming iss=B (cross-broker impersonation).
        self._keys: dict[str, tuple[str, dict[str, Any]]] = {}
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def prime(self) -> None:
        self.refresh(force=True)

    def resolve_key(self, kid: str) -> tuple[str, dict[str, Any]] | None:
        """Return (issuer, JWK) for kid, refreshing once on a miss (key rotation)."""
        entry = self._find(kid)
        if entry is not None:
            return entry
        self.refresh(force=True)
        return self._find(kid)

    def refresh(self, *, force: bool = False) -> None:
        with self._lock:
            now = time.time()
            if not force and self._keys and now < self._expires_at:
                return
            urls = _issuer_jwks_urls(self._settings)
            if not urls:
                logger.warning("brigade_jwks_fetch_failed", reason="jwks-url-unconfigured")
                return
            merged: dict[str, tuple[str, dict[str, Any]]] = {}
            fetched_any = False
            for issuer, jwks_url in urls.items():
                try:
                    with httpx.Client(timeout=10) as client:
                        response = client.get(jwks_url)
                        response.raise_for_status()
                        jwks = cast(dict[str, Any], response.json())
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning(
                        "brigade_jwks_fetch_failed",
                        reason="jwks-fetch-failed",
                        issuer=issuer,
                        error=str(exc),
                    )
                    continue
                fetched_any = True
                for key in jwks.get("keys", []) or []:
                    if isinstance(key, dict) and isinstance(key.get("kid"), str):
                        kid = cast(str, key["kid"])
                        existing = merged.get(kid)
                        if existing is not None and existing[0] != issuer:
                            # kids are expected unique per broker; a cross-issuer
                            # collision means one key would shadow the other. Log
                            # it — the shadowed issuer's tokens for this kid then
                            # fail the signature check, never a silent mis-trust.
                            logger.warning(
                                "brigade_jwks_kid_collision",
                                kid=kid,
                                issuer=issuer,
                                shadows_issuer=existing[0],
                            )
                        merged[kid] = (issuer, cast(dict[str, Any], key))
            if not fetched_any:
                # Every issuer's JWKS fetch failed (transient outage): keep the
                # prior keys rather than blanking the cache and rejecting all
                # tokens until the next refresh.
                return
            self._keys = merged
            self._expires_at = now + BRIGADE_JWKS_TTL_SECONDS
            logger.info(
                "brigade_jwks_refreshed",
                keys=len(merged),
                issuers=len(urls),
                ttl_s=BRIGADE_JWKS_TTL_SECONDS,
            )

    def _find(self, kid: str) -> tuple[str, dict[str, Any]] | None:
        now = time.time()
        if not self._keys or now >= self._expires_at:
            self.refresh(force=False)
        return self._keys.get(kid)


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

    issuers = _trusted_issuers(settings)

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

    entry = jwks_cache.resolve_key(kid)
    if entry is None:
        return _reject("kid-not-found", kid=kid)
    key_issuer, key = entry

    try:
        # Build the RSA verification key from the JWK dict. alg is RS256 (the
        # only algorithm we accept — see ALLOWED_BRIGADE_ALGORITHMS).
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
            issuer=tuple(issuers),
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
    if claims.get("iss") not in issuers:
        return _reject("iss-mismatch", kid=kid)
    # Bind the signing key to the claimed issuer: the kid that signed this token
    # must belong to the JWKS of the issuer the token CLAIMS. Without this, a
    # trusted broker A could sign a token claiming iss=B (another trusted broker)
    # and impersonate B's principals — the JWKS merge alone only proves "signed
    # by SOME trusted broker", not "by the one it claims".
    if claims.get("iss") != key_issuer:
        return _reject(
            "iss-key-mismatch",
            kid=kid,
            claimed_iss=str(claims.get("iss")),
            key_iss=key_issuer,
        )
    if not claims.get("sub"):
        return _reject("missing-sub", kid=kid)
    # Canonical tenancy claim is workspace_id (SUITE-IDENTITY 0.1; Brigade
    # retired org_id). Accept either; the legacy org_id is a fallback.
    if not (claims.get("workspace_id") or claims.get("org_id")):
        return _reject("missing-workspace-id", kid=kid)

    return BrigadeJWTVerificationResult(valid=True, claims=claims, reason=None)


def _reject(reason: str, **fields: Any) -> BrigadeJWTVerificationResult:
    logger.warning("brigade_jwt_rejected", reason=reason, **fields)
    return BrigadeJWTVerificationResult(valid=False, claims=None, reason=reason)
