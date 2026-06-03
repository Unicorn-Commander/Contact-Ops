"""Subscription-tier read + cache (Phase: billing-ops-center).

Contact-Ops does **not** own billing; Ops-Center (Stripe + Lago) is the source
of truth for a user's subscription tier. This module reads that tier for the
authenticated caller and caches it briefly so a per-request tier check (e.g. the
paid-feature gate in :mod:`contact_ops.api.deps`) does not hit Ops-Center on
every request.

Design properties:

  * **Reuses the proven /status path.** The actual network call + response
    normalization is :func:`contact_ops.api.billing.fetch_subscription_status`
    (the same logic that backs ``GET /api/v1/billing/status``). We do not
    duplicate the Ops-Center contract here.
  * **Cached per uc_uid, short TTL (~60s).** A user's tier rarely changes within
    a minute; caching keeps the gate cheap while still picking up an upgrade
    within ~1 minute of checkout completing.
  * **Never throws to the caller.** :func:`get_user_tier` is the public entry
    point for the gate. A read failure (transport error, non-200, malformed
    body) degrades to the user's *last-known cached tier*; if there is none, it
    degrades to ``"free"``. It returns a string, always — it cannot raise.
  * **Inert when billing is unconfigured.** With ``OPS_CENTER_URL`` /
    ``OPS_CENTER_SERVICE_KEY`` unset, ``fetch_subscription_status`` returns
    ``tier="free"`` with no network call, so an unconfigured deployment reads
    every user as ``"free"`` (and, with the gate flag off, still lets everyone
    through — see :mod:`contact_ops.api.deps`).

Tier ladder (ascending). ``"free"`` is rank 0; anything Ops-Center reports that
we do not recognize is treated as ``"free"`` (rank 0) so an unknown tier can
never silently satisfy a paid gate (fail-closed for entitlement).
"""

from __future__ import annotations

import time

import structlog
from fastapi import Request

from contact_ops.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

# Ascending tier ladder. Mirrors the ecosystem 5-tier matrix
# (Free/Basic/Pro/Suite/Enterprise). Unknown tiers -> "free" (rank 0), so a
# tier string we don't understand can never satisfy a paid gate.
_TIER_RANK: dict[str, int] = {
    "free": 0,
    "basic": 1,
    "pro": 2,
    "suite": 3,
    "enterprise": 4,
}

#: The minimum paid tier the "Ask the Analyst" server feature requires.
PRO_TIER = "pro"

#: Per-uc_uid tier cache TTL. Short by design: a tier change (e.g. a completed
#: checkout) is reflected within this window without any cache bust.
TIER_CACHE_TTL_SECONDS = 60.0

# uc_uid -> (tier, fetched_at_monotonic). Process-local; that is sufficient here
# because the gate is advisory entitlement (not a security boundary) and each
# replica re-reads within the TTL. Never grows unbounded in practice (one entry
# per active user); we do not evict because the working set is small and a stale
# entry is the intended last-known-good fallback.
_CACHE: dict[str, tuple[str, float]] = {}


def tier_rank(tier: str | None) -> int:
    """Rank a tier string on the ascending ladder; unknown/None -> 0 (free)."""
    if not tier:
        return 0
    return _TIER_RANK.get(str(tier).strip().lower(), 0)


def tier_at_least(tier: str | None, minimum: str) -> bool:
    """True iff ``tier`` is at or above ``minimum`` on the ladder."""
    return tier_rank(tier) >= tier_rank(minimum)


def _cache_get(uc_uid: str) -> str | None:
    """Return the cached tier for ``uc_uid`` if still within TTL, else None."""
    entry = _CACHE.get(uc_uid)
    if entry is None:
        return None
    tier, fetched_at = entry
    if (time.monotonic() - fetched_at) <= TIER_CACHE_TTL_SECONDS:
        return tier
    return None


def _cache_last_known(uc_uid: str) -> str | None:
    """Return the last-known cached tier for ``uc_uid`` ignoring TTL (fallback)."""
    entry = _CACHE.get(uc_uid)
    return entry[0] if entry is not None else None


def _cache_put(uc_uid: str, tier: str) -> None:
    _CACHE[uc_uid] = (tier, time.monotonic())


def reset_tier_cache() -> None:
    """Clear the tier cache. Test/ops hook only; not used in the request path."""
    _CACHE.clear()


async def get_user_tier(request: Request, settings: Settings | None = None) -> str:
    """Resolve the authenticated user's subscription tier. **Never raises.**

    Identity comes from the validated JWT claims that
    ``JWTValidationMiddleware`` placed on ``request.state.jwt_claims``
    (``uc_uid`` == Keycloak ``sub`` for KC tokens; the federated ``sub`` for
    Brigade/PAT). The actual Ops-Center read + normalization is delegated to
    :func:`contact_ops.api.billing.fetch_subscription_status`.

    Resolution order:

    1. Fresh cache hit (within :data:`TIER_CACHE_TTL_SECONDS`) -> return it.
    2. Otherwise read from Ops-Center; on success, cache + return the tier.
    3. On *any* failure (no identity, transport error, non-200, malformed
       body), fall back to the last-known cached tier for this user.
    4. If there is no cached tier at all, return ``"free"``.

    The return is always a lowercase-able tier string; callers rank it with
    :func:`tier_rank` / :func:`tier_at_least`.
    """
    settings = settings or get_settings()

    # Resolve identity defensively. If claims are missing/unidentifiable we
    # cannot key a cache entry or call upstream — treat as "free" (the gate,
    # when enforced, will then 402 with the upgrade hint; when not enforced it
    # is a no-op). We never raise here.
    claims = getattr(request.state, "jwt_claims", None)
    uc_uid = ""
    if isinstance(claims, dict):
        uc_uid = str(claims.get("uc_uid") or claims.get("sub") or "")
    if not uc_uid:
        logger.warning("tier_read_no_identity")
        return "free"

    cached = _cache_get(uc_uid)
    if cached is not None:
        return cached

    # Import here to avoid a module-load import cycle (billing imports config;
    # this module is imported by api.deps which billing does not import, but the
    # local import keeps the dependency direction unambiguous).
    from contact_ops.api.billing import fetch_subscription_status

    try:
        status = await fetch_subscription_status(request, settings)
        tier = str((status or {}).get("tier") or "free")
    except Exception as exc:  # defensive: fetch_* is already non-throwing.
        # Belt-and-suspenders: even if the upstream helper regresses and raises,
        # the gate must not. Degrade to last-known, else free.
        last = _cache_last_known(uc_uid)
        logger.warning(
            "tier_read_failed_unexpected",
            error_type=type(exc).__name__,
            uc_uid=uc_uid,
            fell_back_to=last or "free",
        )
        return last or "free"

    # `fetch_subscription_status` already degrades transport/non-200/parse
    # failures to {tier:"free", configured:False}. If the read did not actually
    # reach Ops-Center (configured False) AND we have a better last-known tier,
    # prefer the last-known value rather than caching a degraded "free" over it.
    if status.get("configured") is False:
        last = _cache_last_known(uc_uid)
        if last is not None:
            return last
        # Unconfigured with no prior knowledge -> genuinely free. Do not cache
        # this so that, once billing is configured, the first real read isn't
        # masked by a TTL window of cached "free".
        return tier

    _cache_put(uc_uid, tier)
    return tier
