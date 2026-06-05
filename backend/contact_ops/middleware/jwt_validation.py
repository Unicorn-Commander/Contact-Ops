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
import hashlib
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from fastapi import Request, Response, status
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from contact_ops.core.config import Settings
from contact_ops.core.database import _user_is_active_member, async_session_maker
from contact_ops.services.brigade_jwt_verifier import verify_brigade_jwt

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
        "/.well-known/mcps.json",
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

        pat_error: str | None = None
        if token.startswith("co_pat_"):
            claims, pat_error, pat_id = await self._validate_pat(token)
            if claims is None:
                return _unauthorized(pat_error or "invalid personal access token")
            if pat_id is not None:
                asyncio.create_task(self._mark_pat_used(pat_id))
        else:
            claims = await self._validate(token)
            if claims is None:
                claims = await self._validate_brigade(token)
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
            from jose.exceptions import ExpiredSignatureError, JWTError
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

        # 4b. azp/client_id allow-list (design §5): only sanctioned Keycloak
        # clients may present a tenant_id claim, so a rogue uchub client cannot
        # forge a workspace binding. Brigade/PAT use separate validation paths.
        # Warn-only until TENANT_CLAIM_AZP_ENFORCE is set, so real azp values can
        # be observed in logs before any hard rejection.
        allowed_azp = self.settings.TENANT_CLAIM_ALLOWED_AZP
        if allowed_azp:
            azp = claims.get("azp") or claims.get("client_id")
            if azp not in allowed_azp:
                if self.settings.TENANT_CLAIM_AZP_ENFORCE:
                    logger.warning(
                        "jwt_azp_rejected", azp=azp, tenant_id=claims.get("tenant_id")
                    )
                    return None
                logger.warning(
                    "jwt_azp_not_allowlisted_warn_only",
                    azp=azp,
                    tenant_id=claims.get("tenant_id"),
                )

        # 5. Resolve the actor identity. Contact-Ops keys identity on the
        # Keycloak subject (the keycloakId) for KC-issued tokens. This is the
        # canonical key per the federation identity model (apps resolve by
        # keycloakId/email) and is exactly what `user_tenant_membership.user_uc_uid`
        # is seeded with. We deliberately do NOT prefer a `uc_uid` claim here:
        # contact-ops-app emits none today, and pinning to `sub` makes the
        # membership key immune to a future stray `uc_uid` mapper silently
        # re-keying every user (which would 403 them under the membership gate).
        # If such a mapper is ever added, the divergence is logged LOUDLY so the
        # change is made deliberately (with memberships re-keyed in lockstep),
        # never silently. Fail-closed if `sub` is absent (same pattern as
        # tenant_id) so downstream can always rely on claims["uc_uid"].
        sub = claims.get("sub")
        claimed_uc_uid = claims.get("uc_uid")
        if claimed_uc_uid and claimed_uc_uid != sub:
            logger.error(
                "jwt_uc_uid_diverges_from_sub",
                note=(
                    "A Keycloak token carried a uc_uid claim that differs from "
                    "sub. Contact-Ops keys membership on sub, so this claim is "
                    "being IGNORED. If uc_uid is now the intended identity, "
                    "re-key user_tenant_membership.user_uc_uid in lockstep "
                    "before honoring it."
                ),
                uc_uid=claimed_uc_uid,
                sub=sub,
                tenant_id=claims.get("tenant_id"),
            )
        claims["uc_uid"] = sub
        if not claims["uc_uid"]:
            logger.warning(
                "jwt_missing_uc_uid", tenant_id=claims.get("tenant_id"), iss=claims.get("iss")
            )
            return None

        return claims

    async def _validate_brigade(self, token: str) -> dict[str, Any] | None:
        brigade_claims = verify_brigade_jwt(token)
        if brigade_claims is None:
            return None

        # Canonical tenancy claim is workspace_id (SUITE-IDENTITY 0.1; Brigade
        # retired org_id). Read workspace_id first, fall back to org_id for any
        # legacy token still in flight.
        tenant_id = str(
            brigade_claims.get("workspace_id") or brigade_claims.get("org_id") or ""
        )
        uc_uid = str(brigade_claims.get("sub") or "")
        if not tenant_id or not uc_uid:
            logger.warning(
                "brigade_identity_missing",
                has_tenant=bool(tenant_id),
                has_sub=bool(uc_uid),
            )
            return None

        try:
            async with async_session_maker() as db:
                # Bind the verified Brigade token's asserted tenant as the RLS
                # context (transaction-local via set_config is_local=true; it
                # never leaks to the pooled connection) so this resolution read
                # satisfies the tenants_select policy (id = current_tenant_id()).
                # The token is already signature/issuer/audience-verified, so
                # trusting its asserted workspace for the existence+metadata read
                # is safe; the same set_config pattern CO uses for normal requests.
                await db.execute(
                    text(
                        "SELECT set_config('app.tenant_id', :tid, true), "
                        "set_config('app.uc_uid', :uid, true)"
                    ),
                    {"tid": tenant_id, "uid": uc_uid},
                )
                row = await db.execute(
                    text(
                        """
                        SELECT id, slug, hipaa_mode
                        FROM tenants
                        WHERE id = CAST(:tenant_id AS uuid)
                          AND deprecated_at IS NULL
                        """
                    ),
                    {"tenant_id": tenant_id},
                )
                tenant = row.mappings().first()
                # Membership gate (design §4.4): a revoked membership must
                # immediately disable Brigade access for this tenant. Reuse the
                # already-open session; check only when the flag is on (no
                # behavior change otherwise). uc_uid here is the federated
                # service-principal sub from the verified Brigade JWT. Fail-closed.
                member_ok = (
                    not self.settings.MEMBERSHIP_GATE_ENFORCED
                    or (
                        tenant is not None
                        and await _user_is_active_member(db, uc_uid, str(tenant["id"]))
                    )
                )
        except Exception as exc:
            logger.warning(
                "brigade_identity_lookup_failed",
                reason="tenant-lookup-failed",
                error=str(exc),
            )
            return None

        if tenant is None:
            logger.warning("brigade_identity_lookup_failed", reason="tenant-not-found")
            return None

        if not member_ok:
            logger.warning(
                "brigade_membership_denied",
                uc_uid=uc_uid,
                tenant_id=str(tenant["id"]),
            )
            return None

        scopes = brigade_claims.get("scopes", [])
        if not isinstance(scopes, list):
            logger.warning("brigade_identity_lookup_failed", reason="scopes-not-list")
            return None
        role = str(brigade_claims.get("role") or _role_from_scopes(scopes)).upper()
        return {
            "sub": uc_uid,
            "uc_uid": uc_uid,
            "tenant_id": str(tenant["id"]),
            "tenant_slug": str(tenant["slug"]),
            "tenant_hipaa": bool(tenant["hipaa_mode"]),
            "iss": self.settings.BRIGADE_TRUSTED_ISSUER,
            "aud": self.settings.BRIGADE_EXPECTED_AUDIENCE,
            "preferred_username": uc_uid,
            "scope": " ".join(str(scope) for scope in scopes),
            "realm_access": {"roles": [role]},
            "brigade_claims": brigade_claims,
        }

    async def _validate_pat(
        self,
        token: str,
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        token_hash = hashlib.sha256(token.encode()).digest()
        try:
            async with async_session_maker() as db:
                row = await db.execute(
                    text(
                        """
                        SELECT id, tenant_id, user_uc_uid, display_name,
                               scopes, expires_at, revoked_at
                        FROM lookup_personal_access_token(:token_hash)
                        """
                    ),
                    {"token_hash": token_hash},
                )
                data = row.mappings().first()
                if data is None:
                    return None, "personal access token not found", None
                if data["revoked_at"] is not None:
                    return None, "personal access token revoked", None
                expires_at = data["expires_at"]
                if expires_at is not None:
                    expiry = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
                    if expiry <= datetime.now(UTC):
                        return None, "personal access token expired", None
                # Membership gate (design §4.4): revoking a user's membership
                # must immediately disable their PATs for this tenant. Reuse the
                # already-open session; check only when the flag is on (no
                # behavior change otherwise). Fail-closed.
                if self.settings.MEMBERSHIP_GATE_ENFORCED and not await _user_is_active_member(
                    db, str(data["user_uc_uid"]), str(data["tenant_id"])
                ):
                    logger.warning(
                        "pat_membership_denied",
                        uc_uid=str(data["user_uc_uid"]),
                        tenant_id=str(data["tenant_id"]),
                    )
                    return None, "personal access token membership revoked", None
        except Exception as exc:
            logger.warning("pat_lookup_failed", error=str(exc))
            return None, "personal access token lookup failed", None

        scopes = list(data["scopes"] or [])
        claims: dict[str, Any] = {
            "sub": str(data["user_uc_uid"]),
            "uc_uid": str(data["user_uc_uid"]),
            "tenant_id": str(data["tenant_id"]),
            "iss": "contact-ops:personal-access-token",
            "aud": self.settings.KEYCLOAK_AUDIENCE,
            "preferred_username": str(data["user_uc_uid"]),
            "scope": " ".join(scopes) if scopes else self._pat_inherited_scope(),
            "realm_access": {"roles": ["STAFF"]},
            "act": {"sub": f"pat:{data['id']}"},
            "pat_id": str(data["id"]),
            "pat_display_name": str(data["display_name"]),
        }
        return claims, None, str(data["id"])

    async def _mark_pat_used(self, pat_id: str) -> None:
        try:
            async with async_session_maker() as db:
                await db.execute(
                    text("SELECT mark_personal_access_token_used(CAST(:pat_id AS uuid))"),
                    {"pat_id": pat_id},
                )
                await db.commit()
        except Exception as exc:
            logger.warning("pat_last_used_update_failed", pat_id=pat_id, error=str(exc))

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

    @staticmethod
    def _pat_inherited_scope() -> str:
        return (
            " ".join(
                [
                    "people:read",
                    "people:write",
                    "orgs:read",
                    "orgs:write",
                    "relationships:read",
                    "relationships:write",
                    "inbox:read",
                    "inbox:write",
                    "connectors:read",
                    "connectors:write",
                    "carddav:read",
                    "carddav:write",
                    "carddav:admin",
                    "contactops:inbox.review",
                    "dedup:read",
                    "dedup:propose",
                    "pat:read",
                    "pat:write",
                    "dedup:apply",
                    "proposal:auto-apply",
                    "proposal:archive",
                ]
            )
        )


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


def _role_from_scopes(scopes: list[Any]) -> str:
    normalized = {str(scope) for scope in scopes}
    if "admin:debug" in normalized or "admin:*" in normalized:
        return "ADMIN"
    # Suite scopes are resource:action (e.g. contacts:write); the legacy
    # convention was write:resource. Accept both for the member+ -> STAFF map.
    if any(scope.startswith("write:") or scope.endswith(":write") for scope in normalized):
        return "STAFF"
    return "CLIENT"


def _unauthorized(reason: str) -> Response:
    return Response(
        content=b'{"detail":"unauthorized"}',
        status_code=status.HTTP_401_UNAUTHORIZED,
        media_type="application/json",
        headers={"WWW-Authenticate": f'Bearer error="invalid_token", error_description="{reason}"'},
    )
