"""Inbound billing webhooks — Lago subscription events drive plan entitlement.

``POST /api/webhooks/lago`` is how a payment event becomes an entitlement change:
Lago fires ``subscription.created`` / ``subscription.started`` when a tenant
subscribes to a plan and ``subscription.terminated`` when it ends, and this
endpoint translates the plan code into a ``tenants.plan_tier`` via the plan
catalog, then writes it (audited, under the admin DSN) through
:func:`contact_ops.billing.plan_admin.set_tenant_plan_tier`.

This is a MONEY path, so authentication and the tier map are strict:

  * AUTH FAILS CLOSED. Real Lago does NOT send a static shared secret — it signs
    every delivery with a per-payload RS256 JWT (its private key) and exposes the
    matching public key at ``GET {LAGO_API_URL}/webhooks/public_key`` (a
    base64-encoded PEM). The DEFAULT auth path therefore verifies the
    ``X-Lago-Signature`` JWT with RS256 against that public key (fetched+cached,
    or a configured PEM in ``LAGO_WEBHOOK_PUBLIC_KEY``) and fails closed on an
    invalid / missing signature (401). When no public key can be obtained the
    endpoint is disabled (503) — an unauthenticated caller can NEVER move a
    tenant's entitlement.
  * STATIC-SECRET path is OPT-IN ONLY. For deployments where a trusted reverse
    proxy / Lago sidecar already validates the real signature and injects a
    static shared secret, set ``LAGO_WEBHOOK_STATIC_SECRET``; when it is set the
    endpoint constant-time-compares that secret against ``X-Lago-Signature`` (or
    ``Authorization: Bearer``) INSTEAD of JWT verification. Blank (the default)
    => real JWT verification.
  * The JWT middleware bypasses ``/api/webhooks`` (Lago cannot send a bearer
    Keycloak JWT); the signature check above is this route's ONLY auth, so it is
    mandatory.
  * UNKNOWN PLAN CODE -> NO CHANGE. ``tier_for_lago_plan_code`` returns None for a
    code we do not recognise; we log and return ``ignored`` rather than guess a
    tier. Only a recognised event type + a recognised plan code moves entitlement.
  * TERMINATION -> free. A terminated subscription drops the tenant to the free
    rung (loss of the paid plan == loss of paid entitlement).
  * A well-formed but unactionable event (unknown tenant, unknown code, unhandled
    type) returns HTTP 200 with a status so Lago does not retry a benign event
    forever; a genuine server error surfaces as 5xx so Lago DOES retry.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import threading
import time
import uuid
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request, status
from jose import jwt as jose_jwt
from jose.exceptions import JWTError

from contact_ops.billing.catalog import DEFAULT_TIER, tier_for_lago_plan_code
from contact_ops.billing.plan_admin import PlanTierError, set_tenant_plan_tier
from contact_ops.core.config import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Webhooks"])

# Lago event types that create/refresh an active paid subscription. Lago has used
# both ``subscription.started`` and ``subscription.created`` across versions; we
# accept both so a version bump does not silently stop granting entitlement.
_ACTIVATION_EVENTS = frozenset({"subscription.created", "subscription.started"})
_TERMINATION_EVENTS = frozenset({"subscription.terminated"})

# Lago signs its webhooks with RS256 (RSA private key). Pin RS256 ONLY — never
# accept ``none`` or an HMAC alg against a public key (JWT alg-confusion surface).
_LAGO_JWT_ALGORITHMS = ["RS256"]
# How long a fetched Lago public key is trusted before a re-fetch. Lago's
# webhook keypair is long-lived; an hour bounds staleness after a rotation.
_LAGO_PUBLIC_KEY_TTL_SECONDS = 60 * 60


def _provided_signature(request: Request) -> str | None:
    """Extract the presented ``X-Lago-Signature`` (or bearer) value.

    For the JWT path this is Lago's RS256 token; for the static-secret path it is
    the injected shared secret. Prefers ``X-Lago-Signature``; falls back to a
    bearer token in ``Authorization``. Returns None when neither is present.
    """
    sig = request.headers.get("X-Lago-Signature")
    if sig:
        return sig
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _authenticate_static(request: Request, configured_secret: str) -> None:
    """Constant-time shared-secret check (opt-in proxy path). Raises 401.

    ``configured_secret`` is guaranteed non-empty by the caller.
    """
    provided = _provided_signature(request)
    if not provided or not hmac.compare_digest(provided, configured_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook signature",
        )


def _normalize_pem(raw: str) -> str | None:
    """Normalise a public key blob to a PEM string, or None if unusable.

    Accepts a raw PEM (already contains a ``-----BEGIN`` marker) or the
    base64-encoded PEM that Lago's ``/webhooks/public_key`` endpoint returns.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if "-----BEGIN" in raw:
        return raw
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return decoded if "-----BEGIN" in decoded else None


class _LagoPublicKeyCache:
    """Thread-safe, TTL'd cache of Lago's webhook public key PEM.

    Resolution order per :func:`get`: an explicitly-configured PEM
    (``LAGO_WEBHOOK_PUBLIC_KEY``) wins and is never fetched; otherwise the key is
    fetched from ``{LAGO_API_URL}/webhooks/public_key`` and cached for
    :data:`_LAGO_PUBLIC_KEY_TTL_SECONDS`. On a transient fetch failure the last
    good key is kept rather than blanking the cache (avoids rejecting genuine
    deliveries during a brief Lago outage).
    """

    def __init__(self) -> None:
        self._pem: str | None = None
        self._source: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get(self, settings: Any) -> str | None:
        # Configured PEM short-circuits — no network, no cache staleness.
        configured = _normalize_pem(getattr(settings, "LAGO_WEBHOOK_PUBLIC_KEY", "") or "")
        if configured:
            return configured

        api_url = (getattr(settings, "LAGO_API_URL", "") or "").strip()
        if not api_url:
            return None

        with self._lock:
            now = time.time()
            if self._pem and self._source == api_url and now < self._expires_at:
                return self._pem
            fetched = _fetch_lago_public_key(api_url)
            if fetched is None:
                # Keep the prior key (if any and still same source) through a
                # transient failure; otherwise there is nothing to serve.
                if self._pem and self._source == api_url:
                    return self._pem
                return None
            self._pem = fetched
            self._source = api_url
            self._expires_at = now + _LAGO_PUBLIC_KEY_TTL_SECONDS
            return self._pem

    def reset(self) -> None:
        with self._lock:
            self._pem = None
            self._source = None
            self._expires_at = 0.0


_public_key_cache = _LagoPublicKeyCache()


def _fetch_lago_public_key(api_url: str) -> str | None:
    """Fetch + normalise Lago's webhook public key PEM, or None on failure."""
    url = f"{api_url.rstrip('/')}/webhooks/public_key"
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(url)
            response.raise_for_status()
            pem = _normalize_pem(response.text)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("lago_public_key_fetch_failed", url=url, error=str(exc))
        return None
    if pem is None:
        logger.warning("lago_public_key_unparseable", url=url)
    return pem


def _authenticate_jwt(request: Request, public_key_pem: str) -> None:
    """Verify the ``X-Lago-Signature`` RS256 JWT against Lago's public key.

    Fails closed (401) on a missing token or any signature/decode error. Only
    RS256 is accepted; ``none`` and HMAC algs are rejected before decode.
    """
    token = _provided_signature(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing webhook signature",
        )
    try:
        header = jose_jwt.get_unverified_header(token)
    except JWTError as exc:
        logger.warning("lago_webhook_bad_jwt_header", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook signature",
        ) from exc
    if header.get("alg") not in _LAGO_JWT_ALGORITHMS:
        logger.warning("lago_webhook_alg_rejected", alg=header.get("alg"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook signature",
        )
    try:
        # Signature (+ exp, if present) verification. Lago's token carries an
        # ``iss``/``data`` payload we do not need to trust here — the RSA
        # signature against Lago's public key is the authenticity proof. Audience
        # is not asserted by Lago, so do not require it.
        jose_jwt.decode(
            token,
            public_key_pem,
            algorithms=_LAGO_JWT_ALGORITHMS,
            options={"verify_aud": False},
        )
    except JWTError as exc:
        logger.warning("lago_webhook_signature_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook signature",
        ) from exc


def _authenticate(request: Request, settings: Any) -> None:
    """Authenticate an inbound Lago webhook. Raises 503/401 on failure.

    Default: verify the per-payload RS256 JWT against Lago's public key
    (configured PEM or the fetched ``/webhooks/public_key`` endpoint); 503 when no
    key can be obtained (endpoint disabled). Opt-in: when
    ``LAGO_WEBHOOK_STATIC_SECRET`` is set, constant-time-compare that shared
    secret instead (proxy-injected deployments).
    """
    static_secret = (getattr(settings, "LAGO_WEBHOOK_STATIC_SECRET", "") or "").strip()
    if static_secret:
        _authenticate_static(request, static_secret)
        return

    public_key_pem = _public_key_cache.get(settings)
    if not public_key_pem:
        logger.warning("lago_webhook_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="lago webhook not configured",
        )
    _authenticate_jwt(request, public_key_pem)


def _resolve_tier(webhook_type: str, plan_code: str | None) -> str | None:
    """Map a Lago event + plan code to a target tier, or None if unactionable.

    Activation events resolve the plan code through the catalog reverse map
    (unknown code -> None -> caller ignores). Termination always resolves to the
    free rung. Any other event type is unhandled -> None.
    """
    if webhook_type in _TERMINATION_EVENTS:
        return DEFAULT_TIER
    if webhook_type in _ACTIVATION_EVENTS:
        return tier_for_lago_plan_code(plan_code or "")
    return None


@router.post("/api/webhooks/lago")
async def lago_webhook(request: Request) -> dict[str, Any]:
    settings = get_settings()

    # Fail closed: no verifiable signature (no public key AND no static secret)
    # => the endpoint is disabled, never open.
    _authenticate(request, settings)

    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001 — malformed body is a 400, not a 500
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid JSON body",
        ) from exc
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook body must be a JSON object",
        )

    webhook_type = str(body.get("webhook_type") or "")
    subscription = body.get("subscription")
    if not isinstance(subscription, dict):
        # Well-formed but not a subscription event we handle — ack so Lago does
        # not retry a benign delivery.
        logger.info("lago_webhook_no_subscription", webhook_type=webhook_type)
        return {"status": "ignored", "reason": "no subscription object"}

    external_customer_id = subscription.get("external_customer_id")
    plan_code = subscription.get("plan_code")

    target_tier = _resolve_tier(webhook_type, plan_code)
    if target_tier is None:
        logger.info(
            "lago_webhook_unactionable",
            webhook_type=webhook_type,
            plan_code=plan_code,
        )
        return {
            "status": "ignored",
            "reason": "unhandled event type or unknown plan code",
        }

    # external_customer_id is the tenant uuid (ensure_customer sets Lago's
    # external_id = str(tenant_id)). A malformed / missing id is unactionable.
    try:
        tenant_id = uuid.UUID(str(external_customer_id))
    except (ValueError, TypeError, AttributeError):
        logger.warning(
            "lago_webhook_bad_customer_id",
            webhook_type=webhook_type,
            external_customer_id=external_customer_id,
        )
        return {"status": "ignored", "reason": "missing/invalid external_customer_id"}

    try:
        change = await set_tenant_plan_tier(
            tenant_id=tenant_id,
            new_tier=target_tier,
            settings=settings,
            actor="lago_webhook",
            reason=f"lago {webhook_type} plan_code={plan_code}",
            source="lago_webhook",
        )
    except PlanTierError as exc:
        # Unknown tenant / missing admin DSN. An unknown tenant is a benign ack
        # (nothing to do); a missing admin DSN is an operator misconfiguration we
        # surface as 500 so it is loud and Lago retries once it is fixed.
        msg = str(exc)
        if "not found" in msg:
            logger.warning(
                "lago_webhook_unknown_tenant",
                webhook_type=webhook_type,
                tenant_id=str(tenant_id),
            )
            return {"status": "ignored", "reason": "unknown tenant"}
        logger.error("lago_webhook_write_unavailable", error=msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="plan write unavailable",
        ) from exc

    return {
        "status": "applied" if change.changed else "noop",
        "tenant_id": str(tenant_id),
        "before": change.before,
        "after": change.after,
        "event_id": str(change.event_id) if change.event_id else None,
    }
