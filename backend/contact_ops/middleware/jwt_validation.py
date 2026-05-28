"""Keycloak JWT validation middleware.

Validates RS256 JWTs against Keycloak JWKS. Fails closed: when Keycloak is
unconfigured and STANDALONE_MODE is not explicitly enabled in a non-prod ENV,
every request is rejected. Captures the full `act`-claim chain for downstream
OBO propagation per design doc §3 (System Topology) and §6 (Agent Topology).

Validated claims are placed on `request.state.jwt_claims`. Downstream code
reads identity from there; NEVER from request headers (which can be spoofed
upstream of the in-app validation step).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog
from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from contact_ops.core.config import Settings

logger = structlog.get_logger(__name__)


class JWTValidationMiddleware(BaseHTTPMiddleware):
    """Validate a bearer JWT on every request and stash claims on request.state.

    Paths bypassed (no auth required):
      - `/health`, `/health/live`, `/health/ready` — liveness/readiness probes
      - `/docs`, `/redoc`, `/openapi.json` — OpenAPI surfaces
      - `/` — root info endpoint
      - OPTIONS preflight (CORS)

    The MCP endpoint at `/mcp` is NOT bypassed (Phase 0 reviews flagged the
    prior implementation's bypass as a security gap; tenant must come from the
    JWT claim, never from an X-Tenant-Id header).
    """

    SKIP_PATHS = frozenset({
        "/health",
        "/health/live",
        "/health/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/",
        # Prometheus scrape of the agent-fleet metrics registry. The endpoint
        # is read-only and exposes only ``contactops_agent_*`` series. Network
        # ACLs on the scrape source remain the enforcement boundary.
        "/metrics",
    })

    # Prefix-based bypass. CardDAV runs HTTP Basic auth against per-device
    # app passwords rather than bearer JWTs (iOS Contacts.app cannot send
    # bearer tokens). Every request under ``/carddav`` and the discovery
    # endpoint at ``/.well-known/carddav`` therefore bypasses JWT and is
    # authenticated inline by :mod:`contact_ops.carddav.auth`.
    #
    # OAuth callbacks for the M365/Gmail connectors are top-level browser
    # navigations from the provider (no bearer header possible). The
    # endpoint authenticates the originating user by validating the signed
    # ``state`` parameter that was issued at oauth-start time and stored
    # in ``oauth_pending`` (or short-TTL Redis); see
    # :mod:`contact_ops.connectors.oauth_state`.
    SKIP_PREFIXES: tuple[str, ...] = (
        "/carddav",
        "/.well-known/carddav",
        "/api/connectors/m365/oauth/callback",
        "/api/connectors/gmail/oauth/callback",
    )

    def __init__(self, app: Any, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self._jwks: dict[str, Any] | None = None
        self._jwks_expires: float = 0.0
        self._jwks_lock = asyncio.Lock()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if (
            path in self.SKIP_PATHS
            or request.method == "OPTIONS"
            or any(path == p or path.startswith(p + "/") for p in self.SKIP_PREFIXES)
        ):
            return await call_next(request)

        # Standalone mode bypass — only honored when explicitly enabled AND not
        # in prod (enforced by Settings validator). Synthesizes a fake claim
        # set so downstream code can run end-to-end against a dev DB without a
        # Keycloak.
        if self.settings.STANDALONE_MODE:
            request.state.jwt_claims = self._standalone_claims()
            request.state.actor_chain = request.state.jwt_claims
            request.state.human_authority = self.settings.STANDALONE_USER_ID
            return await call_next(request)

        token = self._extract_bearer(request)
        if not token:
            return _unauthorized("missing bearer token")

        claims = await self._validate(token)
        if claims is None:
            return _unauthorized("invalid token")

        request.state.jwt_claims = claims
        request.state.actor_chain = _build_actor_chain(claims)
        request.state.human_authority = _root_subject(claims)
        return await call_next(request)

    # ---------- token extraction + validation ----------

    @staticmethod
    def _extract_bearer(request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        token = auth[7:].strip()
        return token or None

    async def _validate(self, token: str) -> dict[str, Any] | None:
        try:
            from jose import jwt
            from jose.exceptions import JWTError, ExpiredSignatureError
        except ImportError:
            logger.error("python-jose not installed; cannot validate JWT")
            return None

        # 1. Unverified header — but check alg first, before doing anything
        # with the kid (defense against alg-confusion attacks).
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            logger.warning("jwt_header_unparseable", error=str(exc))
            return None

        alg = header.get("alg")
        if alg not in self.settings.JWT_ALLOWED_ALGORITHMS:
            logger.warning("jwt_alg_rejected", alg=alg)
            return None

        # 2. Resolve key from JWKS (with kid-miss refresh)
        kid = header.get("kid")
        key = await self._resolve_key(kid)
        if key is None:
            logger.warning("jwt_kid_not_in_jwks", kid=kid)
            return None

        # 3. Decode + verify signature, issuer, audience, exp/nbf
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=self.settings.JWT_ALLOWED_ALGORITHMS,
                audience=self.settings.KEYCLOAK_AUDIENCE,
                issuer=self.settings.KEYCLOAK_ISSUER,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_at_hash": False,
                    "require_exp": True,
                    "require_iss": True,
                    "require_aud": True,
                },
            )
        except ExpiredSignatureError:
            logger.info("jwt_expired")
            return None
        except JWTError as exc:
            logger.warning("jwt_invalid", error=str(exc))
            return None

        # 4. Required custom claim: tenant_id
        if not claims.get("tenant_id"):
            logger.warning(
                "jwt_missing_tenant_id", sub=claims.get("sub"), iss=claims.get("iss")
            )
            return None

        return claims

    # ---------- JWKS cache ----------

    async def _resolve_key(self, kid: str | None) -> dict[str, Any] | None:
        key = self._find_in_cached_jwks(kid)
        if key is not None:
            return key

        # Cache miss OR kid not in current JWKS — force refresh.
        await self._refresh_jwks()
        return self._find_in_cached_jwks(kid)

    def _find_in_cached_jwks(self, kid: str | None) -> dict[str, Any] | None:
        if self._jwks is None:
            return None
        for k in self._jwks.get("keys", []):
            if k.get("kid") == kid:
                return k
        return None

    async def _refresh_jwks(self) -> None:
        """Fetch JWKS from Keycloak. Coalesces concurrent refreshes."""
        async with self._jwks_lock:
            now = time.time()
            # If another coroutine just refreshed, skip.
            if self._jwks is not None and now < self._jwks_expires - 5:
                return

            url = self.settings.KEYCLOAK_JWKS_URL
            if not url:
                logger.error("jwks_url_unconfigured")
                return

            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    self._jwks = resp.json()
                    self._jwks_expires = now + self.settings.JWKS_CACHE_TTL_SECONDS
                    logger.info(
                        "jwks_refreshed",
                        keys=len(self._jwks.get("keys", [])),
                        ttl_s=self.settings.JWKS_CACHE_TTL_SECONDS,
                    )
            except httpx.HTTPError as exc:
                logger.warning("jwks_fetch_failed", url=url, error=str(exc))

    # ---------- standalone-mode claims ----------

    def _standalone_claims(self) -> dict[str, Any]:
        return {
            "sub": self.settings.STANDALONE_USER_ID,
            "iss": "standalone://contact-ops",
            "aud": self.settings.KEYCLOAK_AUDIENCE,
            "tenant_id": self.settings.STANDALONE_TENANT_ID,
            "tenant_slug": "standalone",
            "tenant_hipaa": False,
            "email": "REMOVED",
            "preferred_username": "standalone-user",
            "uc_uid": self.settings.STANDALONE_USER_ID,
        }


# ---------- helpers (module level so they're easy to test) ----------


def _build_actor_chain(claims: dict[str, Any]) -> dict[str, Any]:
    """Return the full nested actor chain from the claims.

    OBO chain encoding per RFC 8693 + design doc §3 + §6.3:

        {"sub": user, "act": {"sub": agent, "act": {"sub": upstream}}}

    The chain is preserved verbatim — Contact-Ops stores it in
    `action_event.actor` JSONB so audit queries can reconstruct
    'who, on behalf of whom, via which intermediate'.
    """
    chain: dict[str, Any] = {"sub": claims.get("sub")}
    act = claims.get("act")
    if isinstance(act, dict):
        chain["act"] = _build_actor_chain(act)
    return chain


def _root_subject(claims: dict[str, Any]) -> str:
    """Walk the actor chain to find the root (human) subject.

    Used to denormalize `human_authority` on action_event so 'show me
    everything Aaron did, regardless of which agent did the writing' is
    a single index scan.
    """
    current: dict[str, Any] | None = claims
    while current is not None:
        sub = current.get("sub")
        act = current.get("act")
        if not isinstance(act, dict):
            return str(sub) if sub else ""
        current = act
    return ""


def _unauthorized(reason: str) -> Response:
    return Response(
        content=b'{"detail":"unauthorized"}',
        status_code=status.HTTP_401_UNAUTHORIZED,
        media_type="application/json",
        headers={"WWW-Authenticate": f'Bearer error="invalid_token", error_description="{reason}"'},
    )
