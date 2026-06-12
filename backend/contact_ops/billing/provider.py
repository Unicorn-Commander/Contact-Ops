"""Billing provider abstraction — suite-Lago metering, DORMANT by default.

The provider is the seam between Contact-Ops' compute-gated tools and the suite
billing substrate (one shared Lago at https://billing-api.magicunicorn.dev). It
exists so a metered pro op (a GPU agent run) can emit ONE usage event without the
tool code knowing whether billing is wired up, which backend it talks to, or
whether this is a hosted cell or a sovereign self-host.

Design posture — copied verbatim from the entitlement gate + the email
PostmarkProvider, the two surfaces that already got the "dormant + shadow-first"
treatment in this codebase:

  * EVERYTHING OFF BY DEFAULT. ``settings.BILLING_PROVIDER`` defaults to
    ``"manual"`` → a Noop that logs ``billing_would_emit`` and returns, with ZERO
    external calls. Deploying this module changes nothing until ops flips a flag.
  * The Lago provider SELF-DISABLES to Noop behavior when ``LAGO_API_URL`` or
    ``LAGO_API_KEY`` is blank — exactly like ``email/factory.is_configured`` and
    ``keycloak_admin.is_configured``. So selecting ``"lago"`` before the env is
    populated is safe (it just logs would-emit, same as manual).
  * METERING IS BEST-EFFORT AND NEVER RAISES. A Lago hiccup must never 500 a
    tool. Every network path swallows to a structured ``warning`` log with a
    short timeout, mirroring ``keycloak_admin._mint_admin_token`` and the
    ``PostmarkProvider.send_template`` ``except Exception`` swallow.
  * SELF-HOST BYPASSES METERING ENTIRELY. The dispatch caller checks
    ``settings.DEPLOYMENT_MODE == "self_host"`` and simply does not invoke the
    provider (their cell, their data — same rule entitlement applies). The
    provider stays metering-agnostic; the bypass lives at the call site, but the
    Noop default means even a stray call from a self-host box is inert.

The free/paid line is "gate on compute": the billable ops are EXACTLY the
``_PRO_TOOLS`` (GPU work). The CO billable metric code for one metered op is
``contact_ops_agent_run`` (see ``AGENT_RUN_METRIC``). The shared plan contract
(``PlanSpec`` / ``PLAN_CATALOG``) lives in ``contact_ops.billing.catalog`` and is
imported, never re-declared, so every billing surface keys off the SAME tier
strings as ``TIER_LADDER``.

Lago event envelope (suite contract):

    POST {LAGO_API_URL}/api/v1/events
    Authorization: Bearer <LAGO_API_KEY>
    {"event": {
        "transaction_id": "<unique idem key>",
        "external_customer_id": "<tenant_id uuid str>",
        "code": "contact_ops_agent_run",
        "timestamp": <unix int>,
        "properties": {...}
    }}

  success = 200/201/204; 422 = unknown customer (debug-log, do NOT retry — a
  free-tier tenant simply has no Lago customer yet). The canonical client shape
  is Brigade's ``/app/app/integrations/lago.py``.
"""

from __future__ import annotations

import time
import uuid
from typing import Protocol, runtime_checkable

import httpx
import structlog

from contact_ops.billing.catalog import AGENT_RUN_METRIC
from contact_ops.core.config import Settings
from contact_ops.mcp import entitlement

logger = structlog.get_logger(__name__)

# Suite Lago billable metric for one metered Contact-Ops GPU agent op. Imported
# (not re-declared) from the shared catalog so emission + plan specs can never
# drift on the metric string. Re-exported here for callers that import it off the
# provider module.
__all__ = ["AGENT_RUN_METRIC", "BillingProvider", "get_billing_provider"]

# Matches the house posture: short, bounded — a billing call must not hang a tool.
# Brigade uses 5s for the Lago leg; we keep ~10s per the module contract, still
# well under any tool-level deadline, and every call swallows on timeout anyway.
_LAGO_TIMEOUT_SECONDS = 10.0

# Lago accepts these as "event recorded"; everything else is a real warning,
# except 422 which is the benign "no such customer yet" (free tier) case.
_LAGO_OK_STATUSES = frozenset({200, 201, 204})


@runtime_checkable
class BillingProvider(Protocol):
    """Contract every billing backend satisfies.

    Methods are best-effort and must never raise — the caller (an MCP tool
    dispatch) treats billing as a side channel, never a gate on the actual work.
    """

    async def emit_usage(
        self,
        tenant_id: str,
        code: str,
        properties: dict | None = None,
        transaction_id: str | None = None,
    ) -> None:
        """Record one billable usage event for ``tenant_id`` under ``code``."""
        ...

    async def ensure_customer(self, tenant_id: str, plan_tier: str) -> None:
        """Best-effort: make sure a Lago customer exists for ``tenant_id``."""
        ...

    async def get_plan_tier(self, tenant_id: str, db) -> str:
        """Resolve the tenant's plan tier (delegates to entitlement by default)."""
        ...


class _BaseBillingProvider:
    """Shared ``get_plan_tier`` so concrete providers don't re-implement it.

    Plan tier is authoritative in Postgres (``tenants.plan_tier``, read under
    RLS), NOT in Lago — Lago is for metering/invoicing, the app owns entitlement.
    So both providers delegate to the entitlement single-source-of-truth.
    """

    async def get_plan_tier(self, tenant_id: str, db) -> str:
        try:
            tid = uuid.UUID(str(tenant_id))
        except (ValueError, TypeError, AttributeError):
            # A malformed id can't be looked up; fail SAFE to the free rung
            # (same direction entitlement.get_tenant_plan_tier fails).
            return entitlement.DEFAULT_TIER
        return await entitlement.get_tenant_plan_tier(db, tid)


class ManualBillingProvider(_BaseBillingProvider):
    """DORMANT default (``BILLING_PROVIDER='manual'``).

    Logs the intended emission and returns. No external calls, ever — the exact
    analogue of ``email/base.NoopProvider``. This is what runs in dev, in tests,
    and in any deployment whose operator hasn't opted into metered billing.
    """

    async def emit_usage(
        self,
        tenant_id: str,
        code: str,
        properties: dict | None = None,
        transaction_id: str | None = None,
    ) -> None:
        logger.info(
            "billing_would_emit",
            code=code,
            tenant_id=tenant_id,
            provider="manual",
        )

    async def ensure_customer(self, tenant_id: str, plan_tier: str) -> None:
        # Noop: there is no external system to provision against.
        logger.info(
            "billing_would_ensure_customer",
            tenant_id=tenant_id,
            plan_tier=plan_tier,
            provider="manual",
        )


class LagoBillingProvider(_BaseBillingProvider):
    """Suite-Lago provider (``BILLING_PROVIDER='lago'`` or ``'federated'``).

    Self-disables to Noop behavior when ``LAGO_API_URL`` or ``LAGO_API_KEY`` is
    blank (``_is_configured``) — so it is safe to SELECT this provider before the
    env is populated; it simply logs ``billing_would_emit`` like manual until the
    secrets land. When configured, it POSTs the suite event envelope with Bearer
    auth, treats 200/201/204 as success and 422 as a benign unknown-customer, and
    NEVER raises (every failure swallows to a structured warning). The API key is
    sent only in the Authorization header and is NEVER logged.
    """

    def __init__(self, settings: Settings) -> None:
        self._api_url = (settings.LAGO_API_URL or "").rstrip("/")
        self._api_key = settings.LAGO_API_KEY or ""

    def _is_configured(self) -> bool:
        return bool(self._api_url and self._api_key)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def emit_usage(
        self,
        tenant_id: str,
        code: str,
        properties: dict | None = None,
        transaction_id: str | None = None,
    ) -> None:
        # Unconfigured → behave exactly like the Noop manual provider.
        if not self._is_configured():
            logger.info(
                "billing_would_emit",
                code=code,
                tenant_id=tenant_id,
                provider="lago",
                reason="unconfigured",
            )
            return

        # uuid4() is fine for idempotency keys in Python (this is NOT the
        # workflow's JS Math.random pitfall); a caller may also pass a stable
        # transaction_id to make a retry idempotent on Lago's side.
        tx = transaction_id or f"contact-ops-{uuid.uuid4()}"
        envelope = {
            "event": {
                "transaction_id": tx,
                "external_customer_id": str(tenant_id),
                "code": code,
                "timestamp": int(time.time()),
                "properties": properties or {},
            }
        }

        try:
            async with httpx.AsyncClient(timeout=_LAGO_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    f"{self._api_url}/api/v1/events",
                    headers=self._auth_headers(),
                    json=envelope,
                )
        except Exception as exc:  # noqa: BLE001 — metering must never raise
            logger.warning(
                "billing_emit_errored",
                code=code,
                tenant_id=tenant_id,
                error=type(exc).__name__,
            )
            return

        if resp.status_code in _LAGO_OK_STATUSES:
            return
        if resp.status_code == 422:
            # No matching Lago customer (e.g. a free-tier tenant never
            # provisioned). Benign + non-retryable — debug, don't warn.
            logger.debug(
                "billing_emit_unknown_customer",
                code=code,
                tenant_id=tenant_id,
            )
            return
        logger.warning(
            "billing_emit_failed",
            code=code,
            tenant_id=tenant_id,
            status=resp.status_code,
        )

    async def ensure_customer(self, tenant_id: str, plan_tier: str) -> None:
        """Best-effort customer provisioning. Self-disables when unconfigured.

        POSTs ``/api/v1/customers`` so a tenant exists in Lago before its first
        metered event (otherwise emit_usage gets a benign 422). Subscribing the
        customer to a plan is left to the billing wiring that owns plan codes —
        this just guarantees the customer record. Never raises; the key is never
        logged.
        """
        if not self._is_configured():
            logger.info(
                "billing_would_ensure_customer",
                tenant_id=tenant_id,
                plan_tier=plan_tier,
                provider="lago",
                reason="unconfigured",
            )
            return

        body = {
            "customer": {
                "external_id": str(tenant_id),
                "metadata": [
                    {
                        "key": "plan_tier",
                        "value": str(plan_tier),
                        "display_in_invoice": False,
                    }
                ],
            }
        }
        try:
            async with httpx.AsyncClient(timeout=_LAGO_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    f"{self._api_url}/api/v1/customers",
                    headers=self._auth_headers(),
                    json=body,
                )
        except Exception as exc:  # noqa: BLE001 — provisioning must never raise
            logger.warning(
                "billing_ensure_customer_errored",
                tenant_id=tenant_id,
                error=type(exc).__name__,
            )
            return

        # Lago returns 200/201 on create-or-update; 422 = validation. None of
        # these should ever break the caller — log non-success and move on.
        if resp.status_code in _LAGO_OK_STATUSES:
            return
        logger.warning(
            "billing_ensure_customer_failed",
            tenant_id=tenant_id,
            status=resp.status_code,
        )


# --- Factory ----------------------------------------------------------------
# Module-singleton keyed by provider name. Settings is process-global
# (get_settings is itself lru_cache'd) so a per-name cache is correct and lets a
# test build a fresh provider by clearing _PROVIDER_CACHE. We key on the resolved
# provider NAME (not the Settings object, which isn't hashable) so 'lago' and
# 'federated' share one instance.
_PROVIDER_CACHE: dict[str, BillingProvider] = {}


def get_billing_provider(settings: Settings) -> BillingProvider:
    """Select the billing provider from ``settings.BILLING_PROVIDER``.

    'manual' (default) → ManualBillingProvider (Noop, dormant).
    'lago' | 'federated' → LagoBillingProvider (self-disables to Noop when the
    Lago env is blank). Any unknown value fails SAFE to the Noop manual provider
    so a typo can never accidentally start billing or break a tool.
    """
    name = (settings.BILLING_PROVIDER or "manual").strip().lower()
    cached = _PROVIDER_CACHE.get(name)
    if cached is not None:
        return cached

    if name in ("lago", "federated"):
        provider: BillingProvider = LagoBillingProvider(settings)
    else:
        # 'manual' and any unrecognized value → dormant Noop.
        if name != "manual":
            logger.warning(
                "billing_provider_unknown_defaulting_manual",
                requested=name,
            )
        provider = ManualBillingProvider()

    _PROVIDER_CACHE[name] = provider
    return provider
