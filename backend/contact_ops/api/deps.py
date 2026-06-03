"""Shared FastAPI dependencies (Phase: billing-ops-center).

Currently houses the paid-tier gate used to fence server-side paid features
(the "Ask the Analyst" server chat endpoint) behind a Pro subscription.

The gate is **flag-gated and inert by default**: with
``BILLING_TIER_ENFORCED`` False (the default), :func:`require_pro` /
:func:`require_tier` are a strict NO-OP and every authenticated caller passes,
so the gated endpoint behaves exactly as it does today. Only when an operator
flips ``BILLING_TIER_ENFORCED`` True do callers below the required tier receive
**HTTP 402 Payment Required** with an upgrade hint; Pro+ callers always pass.

Tier is read via :func:`contact_ops.services.tier.get_user_tier`, which reads
Ops-Center (cached ~60s) and **never raises** — it degrades to the user's
last-known tier, or ``"free"``. So the gate's only failure mode under
enforcement is "treat an unreadable tier as below-Pro and 402", never a 5xx.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from fastapi import HTTPException, Request, status

from contact_ops.core.config import get_settings
from contact_ops.services.tier import PRO_TIER, get_user_tier, tier_at_least

logger = structlog.get_logger(__name__)


def require_tier(minimum: str) -> Callable[[Request], Awaitable[None]]:
    """Build a FastAPI dependency that requires at least tier ``minimum``.

    Semantics:

    * **Flag off (``BILLING_TIER_ENFORCED`` False, the default):** strict NO-OP.
      The dependency resolves with no tier read and no error — every
      authenticated caller passes. (Auth itself is still enforced upstream by
      ``JWTValidationMiddleware``; this gate adds nothing when the flag is off.)
    * **Flag on, caller at/above ``minimum``:** passes.
    * **Flag on, caller below ``minimum``:** raises **HTTP 402** with a JSON
      detail carrying ``required_tier``, ``current_tier`` and an
      ``upgrade`` hint pointing the SPA at the on-device alternative / upgrade.

    The returned dependency never raises anything but the 402 (tier reads are
    non-throwing), so a gated endpoint cannot 5xx on account of this gate.
    """

    async def _dependency(request: Request) -> None:
        settings = get_settings()
        # NO-OP when enforcement is off: do not even read the tier. This keeps
        # the gated endpoint byte-for-byte identical to its pre-gate behavior
        # (no Ops-Center call, no added latency) until an operator opts in.
        if not settings.BILLING_TIER_ENFORCED:
            return

        tier = await get_user_tier(request, settings)
        if tier_at_least(tier, minimum):
            return

        logger.info(
            "tier_gate_denied",
            required_tier=minimum,
            current_tier=tier,
            path=request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "tier_required",
                "required_tier": minimum,
                "current_tier": tier,
                "upgrade": (
                    f"This feature requires the {minimum.capitalize()} plan or "
                    "higher. Free users can use the on-device analyst instead, "
                    "or upgrade at /api/v1/billing/checkout."
                ),
            },
        )

    return _dependency


#: Convenience dependency for the most common gate: requires Pro or higher.
#: Use as ``dependencies=[Depends(require_pro)]`` on a route.
require_pro = require_tier(PRO_TIER)
