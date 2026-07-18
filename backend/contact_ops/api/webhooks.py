"""Inbound billing webhooks — Lago subscription events drive plan entitlement.

``POST /api/webhooks/lago`` is how a payment event becomes an entitlement change:
Lago fires ``subscription.created`` / ``subscription.started`` when a tenant
subscribes to a plan and ``subscription.terminated`` when it ends, and this
endpoint translates the plan code into a ``tenants.plan_tier`` via the plan
catalog, then writes it (audited, under the admin DSN) through
:func:`contact_ops.billing.plan_admin.set_tenant_plan_tier`.

This is a MONEY path, so authentication and the tier map are strict:

  * AUTH FAILS CLOSED. The endpoint requires ``LAGO_WEBHOOK_SECRET`` to be
    configured; when it is blank it returns 503 (never processes an
    unauthenticated event). The caller must present the secret in the
    ``X-Lago-Signature`` header (or ``Authorization: Bearer <secret>``), compared
    in constant time. A missing / wrong secret is 401.
  * The JWT middleware bypasses ``/api/webhooks`` (Lago cannot send a bearer JWT);
    the shared-secret check above is this route's ONLY auth, so it is mandatory.
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

import hmac
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, status

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


def _provided_secret(request: Request) -> str | None:
    """Extract the presented webhook secret from the request headers.

    Prefers the ``X-Lago-Signature`` header; falls back to a bearer token in
    ``Authorization``. Returns None when neither is present.
    """
    sig = request.headers.get("X-Lago-Signature")
    if sig:
        return sig
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _authenticate(request: Request, configured_secret: str) -> None:
    """Constant-time shared-secret check. Raises 503/401 on failure.

    ``configured_secret`` is guaranteed non-empty by the caller (the blank case
    is a 503 handled before this runs).
    """
    provided = _provided_secret(request)
    if not provided or not hmac.compare_digest(provided, configured_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook signature",
        )


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

    # Fail closed: no configured secret => the endpoint is disabled, never open.
    configured = settings.LAGO_WEBHOOK_SECRET
    if not configured:
        logger.warning("lago_webhook_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="lago webhook not configured",
        )
    _authenticate(request, configured)

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
