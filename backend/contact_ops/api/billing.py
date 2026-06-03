"""Billing proxy → Ops-Center (Phase: billing-ops-center).

Contact-Ops does **not** run its own Stripe. This router is a *thin proxy* that
defers every billing decision to the central Ops-Center billing brain
(Stripe + Lago). It mirrors the proven cross-app pattern used by
``majiks-studio-web/backend/routers/billing.py``:

  * ``httpx.AsyncClient(base_url=OPS_CENTER_URL, timeout=OPS_CENTER_TIMEOUT)``
  * authenticates to Ops-Center with a **service key** sent as
    ``Authorization: Bearer <key>``
  * identifies the acting user with the ``X-User-ID`` header (Ops-Center maps
    the service-key caller's ``X-User-ID`` to the org/customer for Lago).

Identity source: Contact-Ops's ``JWTValidationMiddleware`` has already
validated the caller's bearer and placed the normalized claims on
``request.state.jwt_claims`` (``uc_uid`` == Keycloak ``sub`` for KC tokens; the
federated ``sub`` for Brigade/PAT). We pass that ``uc_uid`` as ``X-User-ID`` and
the user's ``email`` (when present) as ``X-User-Email`` to help Ops-Center
resolve / create the Lago customer. This matches the federation identity model
(apps resolve by keycloakId/email).

Safety properties (mirroring the switch endpoint's posture):
  * **Inert until configured.** With ``OPS_CENTER_URL`` or
    ``OPS_CENTER_SERVICE_KEY`` empty, ``GET /status`` returns
    ``{"tier": "free", "configured": false}`` with **no** network call, and
    ``POST /checkout`` returns 503. Deploying this code before Ops-Center is
    wired is therefore safe.
  * **The service key never leaks.** It is sent only in the Authorization
    header to Ops-Center; it is never logged, echoed, or returned.
  * **Upstream internals never leak.** Any Ops-Center error or transport
    failure collapses to a generic 502 (``billing upstream unavailable``);
    ``GET /status`` additionally degrades to a safe ``configured:false`` shape
    rather than surfacing a 5xx for a read.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from contact_ops.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    """Body for POST /checkout. ``plan_code`` selects the Ops-Center plan/tier.

    Optional ``success_url`` / ``cancel_url`` let the SPA control the post-
    payment redirect; when omitted, Ops-Center applies its own defaults.
    """

    plan_code: str = Field(..., min_length=1, description="Ops-Center plan/tier code")
    success_url: str | None = None
    cancel_url: str | None = None


class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_configured(settings: Settings) -> bool:
    """True only when BOTH the Ops-Center URL and service key are set."""
    return bool(settings.OPS_CENTER_URL and settings.OPS_CENTER_SERVICE_KEY)


def _identity(request: Request) -> tuple[str, str | None]:
    """Pull (uc_uid, email) from the validated JWT claims on request.state.

    ``JWTValidationMiddleware`` guarantees ``jwt_claims`` is present (it rejects
    unauthenticated requests before any router runs) and that ``uc_uid`` is
    normalized to a non-empty value. We defensively fall back to ``sub`` and
    fail closed with 401 if neither is present.
    """
    claims = getattr(request.state, "jwt_claims", None)
    if not isinstance(claims, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    uc_uid = str(claims.get("uc_uid") or claims.get("sub") or "")
    if not uc_uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user identity unresolved",
        )
    email = claims.get("email")
    return uc_uid, (str(email) if email else None)


def _headers(settings: Settings, uc_uid: str, email: str | None) -> dict[str, str]:
    """Build the Ops-Center auth + identity headers.

    The service key is the Bearer credential (NEVER logged). ``X-User-ID`` is the
    acting user's uc_uid; ``X-User-Email`` is included when known so Ops-Center
    can resolve/create the Lago customer by email if it has no uc_uid mapping.
    """
    headers = {
        "Authorization": f"Bearer {settings.OPS_CENTER_SERVICE_KEY}",
        "X-User-ID": uc_uid,
        "Accept": "application/json",
    }
    if email:
        headers["X-User-Email"] = email
    return headers


def _upstream_502() -> HTTPException:
    """A generic 502 that never leaks Ops-Center internals."""
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="billing upstream unavailable",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def fetch_subscription_status(
    request: Request, settings: Settings | None = None
) -> dict[str, Any]:
    """Read + normalize the caller's subscription status from Ops-Center.

    This is the shared core behind ``GET /api/v1/billing/status``; the tier-gate
    (:mod:`contact_ops.services.tier`) calls it directly so the Ops-Center
    contract lives in exactly one place. Returns::

        {tier, status, plan, portal_url?, configured: bool}

    INERT when unconfigured: returns ``{"tier": "free", ..., "configured": False}``
    with no network call. On any upstream/transport/parse failure, degrades to
    the same safe ``configured: False`` shape — it **does not raise** (a status
    read must never 5xx the caller's billing UI, and the gate relies on this
    helper never throwing). Identity-resolution (``_identity``) can still raise
    401 for a genuinely unauthenticated caller; that is the one expected
    exception and only fires before any network call.
    """
    settings = settings or get_settings()
    if not _is_configured(settings):
        # No URL/key -> inert. No network, no error.
        return {"tier": "free", "status": None, "plan": None, "configured": False}

    uc_uid, email = _identity(request)

    try:
        async with httpx.AsyncClient(
            base_url=settings.OPS_CENTER_URL, timeout=settings.OPS_CENTER_TIMEOUT
        ) as client:
            resp = await client.get(
                "/api/v1/subscriptions/current",
                headers=_headers(settings, uc_uid, email),
            )
    except httpx.HTTPError as exc:
        # Transport failure (DNS/connect/timeout). Degrade, don't 5xx a read.
        logger.warning(
            "billing_status_upstream_transport_error",
            error_type=type(exc).__name__,
            uc_uid=uc_uid,
        )
        return {"tier": "free", "status": None, "plan": None, "configured": False}

    if resp.status_code != 200:
        # Non-200 from Ops-Center. Do NOT echo the body (it may carry upstream
        # internals); log only the status. Degrade to a safe shape.
        logger.warning(
            "billing_status_upstream_non_200",
            upstream_status=resp.status_code,
            uc_uid=uc_uid,
        )
        return {"tier": "free", "status": None, "plan": None, "configured": False}

    try:
        data = resp.json()
    except (ValueError, httpx.HTTPError):
        logger.warning("billing_status_upstream_non_json", uc_uid=uc_uid)
        return {"tier": "free", "status": None, "plan": None, "configured": False}

    if not isinstance(data, dict):
        data = {}

    # Normalize Ops-Center's shape (see subscription_api.get_current_subscription
    # / stripe_client.get_customer_subscription) to our contract. Field names
    # vary across the Lago vs Stripe code paths, so accept the known aliases.
    tier = data.get("tier") or data.get("tier_code") or "free"
    sub_status = data.get("status")
    plan = (
        data.get("plan_name")
        or data.get("plan_code")
        or data.get("plan")
        or data.get("tier_name")
    )
    out: dict[str, Any] = {
        "tier": tier,
        "status": sub_status,
        "plan": plan,
        "configured": True,
    }
    # Customer portal link is optional; only surface it if Ops-Center sent one.
    portal_url = data.get("portal_url") or data.get("customer_portal_url")
    if portal_url:
        out["portal_url"] = portal_url
    return out


@router.get("/status")
async def billing_status(request: Request) -> dict[str, Any]:
    """Current subscription status for the authenticated user.

    Thin endpoint over :func:`fetch_subscription_status` (the shared read +
    normalization). INERT when unconfigured; degrades to a safe
    ``configured: false`` shape on any upstream failure.
    """
    return await fetch_subscription_status(request)


@router.get("/plans")
async def billing_plans(request: Request) -> Any:
    """Available subscription plans.

    Proxies Ops-Center ``GET /api/v1/subscriptions/plans`` and returns its body
    verbatim (the SPA renders Ops-Center's plan list directly). INERT when
    unconfigured: returns ``{"plans": [], "configured": false}`` with no network
    call. Any upstream/transport failure collapses to a generic 502.
    """
    settings = get_settings()
    if not _is_configured(settings):
        return {"plans": [], "configured": False}

    uc_uid, email = _identity(request)

    try:
        async with httpx.AsyncClient(
            base_url=settings.OPS_CENTER_URL, timeout=settings.OPS_CENTER_TIMEOUT
        ) as client:
            resp = await client.get(
                "/api/v1/subscriptions/plans",
                headers=_headers(settings, uc_uid, email),
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "billing_plans_upstream_transport_error",
            error_type=type(exc).__name__,
        )
        raise _upstream_502() from exc

    if resp.status_code != 200:
        logger.warning("billing_plans_upstream_non_200", upstream_status=resp.status_code)
        raise _upstream_502()

    try:
        return resp.json()
    except (ValueError, httpx.HTTPError) as exc:
        logger.warning("billing_plans_upstream_non_json")
        raise _upstream_502() from exc


@router.post("/checkout", response_model=CheckoutResponse)
async def billing_checkout(request: Request, body: CheckoutRequest) -> CheckoutResponse:
    """Create a checkout session via Ops-Center (which owns Stripe).

    Proxies ``POST /api/v1/billing/checkout`` and returns the checkout URL.
    Returns **503** when unconfigured (a write must fail loudly, not silently
    succeed). Any upstream/transport failure collapses to a generic 502.
    """
    settings = get_settings()
    if not _is_configured(settings):
        logger.warning("billing_checkout_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="billing not configured",
        )

    uc_uid, email = _identity(request)

    # Mirror the majiks payload: identify the user in the body too (Ops-Center's
    # checkout reads user_id from the body / metadata), plus the chosen plan and
    # optional redirect URLs.
    payload: dict[str, Any] = {"user_id": uc_uid, "plan_code": body.plan_code}
    if email:
        payload["email"] = email
    if body.success_url:
        payload["success_url"] = body.success_url
    if body.cancel_url:
        payload["cancel_url"] = body.cancel_url

    try:
        async with httpx.AsyncClient(
            base_url=settings.OPS_CENTER_URL, timeout=settings.OPS_CENTER_TIMEOUT
        ) as client:
            resp = await client.post(
                "/api/v1/billing/checkout",
                json=payload,
                headers=_headers(settings, uc_uid, email),
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "billing_checkout_upstream_transport_error",
            error_type=type(exc).__name__,
            uc_uid=uc_uid,
        )
        raise _upstream_502() from exc

    if resp.status_code not in (200, 201):
        logger.warning(
            "billing_checkout_upstream_non_2xx",
            upstream_status=resp.status_code,
            uc_uid=uc_uid,
        )
        raise _upstream_502()

    try:
        data = resp.json()
    except (ValueError, httpx.HTTPError) as exc:
        logger.warning("billing_checkout_upstream_non_json", uc_uid=uc_uid)
        raise _upstream_502() from exc

    if not isinstance(data, dict):
        data = {}
    checkout_url = data.get("checkout_url") or data.get("url")
    if not checkout_url:
        # Ops-Center returned 2xx but no usable URL — treat as an upstream fault.
        logger.warning("billing_checkout_upstream_no_url", uc_uid=uc_uid)
        raise _upstream_502()

    return CheckoutResponse(
        checkout_url=str(checkout_url),
        session_id=(str(data["session_id"]) if data.get("session_id") else None),
    )
