"""Five-layer cost guard (Phase 3 Design §7).

Enforcement at the LiteLLM gateway is the production line of defense. This
module is the in-process pre-check that runs *before* the gateway is hit:
budget violations stop the agent immediately so we never burn tokens we are
about to refuse to pay for.

Layers:
  1. per-request token cap (``max_tokens_per_call``)
  2. per-agent turn counter (``max_turns_per_run``)
  3. per-session budget (``session_budget_cents``)
  4. per-tenant daily / hourly budget (``daily_tenant_budget_cents``)
  5. monthly budget hard-stop + tiered alerts at 75/90/95/100%

Burn is recorded into the ``cost_budget_violation`` table on every block so
the Calibration Daemon and the Slack alerter can replay history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents.errors import CostBudgetExceededError


@dataclass(frozen=True)
class CostBudget:
    """Per-agent budget envelope. All amounts in cents."""

    max_tokens_per_call: int = 1024
    max_turns_per_run: int = 25
    session_budget_cents: int = 50
    daily_tenant_budget_cents: int = 1000
    monthly_tenant_budget_cents: int = 20000

    def __post_init__(self) -> None:
        if self.max_tokens_per_call <= 0:
            raise ValueError("max_tokens_per_call must be positive")
        if self.max_turns_per_run <= 0:
            raise ValueError("max_turns_per_run must be positive")
        if self.session_budget_cents <= 0:
            raise ValueError("session_budget_cents must be positive")
        if self.daily_tenant_budget_cents <= 0:
            raise ValueError("daily_tenant_budget_cents must be positive")
        if self.monthly_tenant_budget_cents <= 0:
            raise ValueError("monthly_tenant_budget_cents must be positive")


# Alert thresholds. The keys are the percent of monthly budget; values are
# the channel labels (``slack``, ``slack+email``, ``hard-stop``).
MONTHLY_ALERT_THRESHOLDS: dict[float, str] = {
    0.75: "slack",
    0.90: "slack",
    0.95: "slack+email",
    1.00: "hard-stop",
}


class CostGuard:
    """In-process budget enforcement.

    Stateful per-run (turn counter, session burn). The daily/monthly burn
    is queried from Postgres so multiple worker processes share the same
    view of tenant spend.
    """

    def __init__(self, *, db: AsyncSession, budget: CostBudget) -> None:
        self.db = db
        self.budget = budget
        self._turns_used = 0
        self._session_cents_used = 0

    @property
    def session_cents_used(self) -> int:
        return self._session_cents_used

    @property
    def turns_used(self) -> int:
        return self._turns_used

    async def check_and_record(
        self,
        *,
        agent_slug: str,
        tenant_id: UUID,
        estimated_tokens: int,
        estimated_cents: int,
    ) -> None:
        """Pre-flight check. Raises CostBudgetExceededError on any violation.

        ``estimated_tokens`` is the caller's pre-call estimate for layer 1.
        ``estimated_cents`` is the post-tokenization cost estimate (from
        LiteLLM's price table) for layers 3-5.
        """
        # Layer 1: per-request token cap.
        if estimated_tokens > self.budget.max_tokens_per_call:
            await self._record_violation(
                agent_slug=agent_slug,
                tenant_id=tenant_id,
                layer=1,
                detail={
                    "estimated_tokens": estimated_tokens,
                    "limit": self.budget.max_tokens_per_call,
                },
            )
            raise CostBudgetExceededError(
                1,
                f"estimated tokens {estimated_tokens} exceeds per-call cap "
                f"{self.budget.max_tokens_per_call}",
                agent_slug=agent_slug,
                tenant_id=str(tenant_id),
            )

        # Layer 2: per-run turn counter.
        if self._turns_used >= self.budget.max_turns_per_run:
            await self._record_violation(
                agent_slug=agent_slug,
                tenant_id=tenant_id,
                layer=2,
                detail={
                    "turns_used": self._turns_used,
                    "limit": self.budget.max_turns_per_run,
                },
            )
            raise CostBudgetExceededError(
                2,
                f"agent turn limit reached ({self.budget.max_turns_per_run})",
                agent_slug=agent_slug,
                tenant_id=str(tenant_id),
            )

        # Layer 3: session budget.
        if self._session_cents_used + estimated_cents > self.budget.session_budget_cents:
            await self._record_violation(
                agent_slug=agent_slug,
                tenant_id=tenant_id,
                layer=3,
                detail={
                    "session_used": self._session_cents_used,
                    "limit": self.budget.session_budget_cents,
                    "would_add": estimated_cents,
                },
            )
            raise CostBudgetExceededError(
                3,
                f"session budget {self.budget.session_budget_cents}c would be "
                f"exceeded ({self._session_cents_used}c used + "
                f"{estimated_cents}c would add)",
                agent_slug=agent_slug,
                tenant_id=str(tenant_id),
            )

        # Layer 4: daily/hourly tenant budget.
        daily_used = await self._burn_since(
            agent_slug=agent_slug,
            tenant_id=tenant_id,
            since=datetime.now(UTC) - timedelta(days=1),
        )
        if daily_used + estimated_cents > self.budget.daily_tenant_budget_cents:
            await self._record_violation(
                agent_slug=agent_slug,
                tenant_id=tenant_id,
                layer=4,
                detail={
                    "daily_used": daily_used,
                    "limit": self.budget.daily_tenant_budget_cents,
                },
            )
            raise CostBudgetExceededError(
                4,
                f"daily tenant budget {self.budget.daily_tenant_budget_cents}c "
                f"would be exceeded ({daily_used}c used + "
                f"{estimated_cents}c would add)",
                agent_slug=agent_slug,
                tenant_id=str(tenant_id),
            )

        # Layer 5: monthly budget.
        monthly_used = await self._burn_since(
            agent_slug=agent_slug,
            tenant_id=tenant_id,
            since=datetime.now(UTC) - timedelta(days=30),
        )
        if monthly_used + estimated_cents > self.budget.monthly_tenant_budget_cents:
            await self._record_violation(
                agent_slug=agent_slug,
                tenant_id=tenant_id,
                layer=5,
                detail={
                    "monthly_used": monthly_used,
                    "limit": self.budget.monthly_tenant_budget_cents,
                },
            )
            raise CostBudgetExceededError(
                5,
                f"monthly tenant budget {self.budget.monthly_tenant_budget_cents}c "
                f"would be exceeded ({monthly_used}c used + "
                f"{estimated_cents}c would add)",
                agent_slug=agent_slug,
                tenant_id=str(tenant_id),
            )

    def record_call(self, *, cents: int) -> None:
        """Record successful LLM call in the per-run counters."""
        self._turns_used += 1
        self._session_cents_used += cents

    async def get_burn_rate(
        self,
        *,
        tenant_id: UUID,
        agent_slug: str | None = None,
        window_seconds: int = 86400,
    ) -> dict[str, float]:
        """Return cents/hour and cents/day burn for telemetry."""
        since = datetime.now(UTC) - timedelta(seconds=window_seconds)
        total = await self._burn_since(
            agent_slug=agent_slug or "*",
            tenant_id=tenant_id,
            since=since,
        )
        hours = max(window_seconds / 3600.0, 1.0)
        return {
            "cents_per_hour": total / hours,
            "cents_per_day": (total / hours) * 24.0,
            "window_cents": float(total),
        }

    async def alert_thresholds(
        self,
        *,
        tenant_id: UUID,
        agent_slug: str,
    ) -> str | None:
        """Return the highest crossed alert label, or None.

        Called by the Calibration Daemon every 20 minutes per design doc §7.
        """
        monthly_used = await self._burn_since(
            agent_slug=agent_slug,
            tenant_id=tenant_id,
            since=datetime.now(UTC) - timedelta(days=30),
        )
        ratio = monthly_used / max(self.budget.monthly_tenant_budget_cents, 1)
        crossed: str | None = None
        for threshold in sorted(MONTHLY_ALERT_THRESHOLDS):
            if ratio >= threshold:
                crossed = MONTHLY_ALERT_THRESHOLDS[threshold]
        return crossed

    async def _burn_since(
        self,
        *,
        agent_slug: str,
        tenant_id: UUID,
        since: datetime,
    ) -> int:
        """Sum cents on prov_activities for this (agent, tenant) since ``since``."""
        if agent_slug == "*":
            stmt = text(
                """
                SELECT COALESCE(SUM(cost_cents), 0)
                FROM prov_activities
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND started_at >= :since
                """
            )
            params = {"tenant_id": str(tenant_id), "since": since}
        else:
            stmt = text(
                """
                SELECT COALESCE(SUM(cost_cents), 0)
                FROM prov_activities
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND agent_id = :agent_slug
                  AND started_at >= :since
                """
            )
            params = {
                "tenant_id": str(tenant_id),
                "agent_slug": agent_slug,
                "since": since,
            }
        result = await self.db.execute(stmt, params)
        return int(result.scalar_one())

    async def _record_violation(
        self,
        *,
        agent_slug: str,
        tenant_id: UUID,
        layer: int,
        detail: dict[str, int],
    ) -> None:
        """Persist a violation row for downstream alerting."""
        await self.db.execute(
            text(
                """
                INSERT INTO cost_budget_violation (
                    tenant_id, agent_slug, layer, detail, occurred_at
                ) VALUES (
                    CAST(:tenant_id AS uuid), :agent_slug, :layer,
                    CAST(:detail AS jsonb), now()
                )
                """
            ),
            {
                "tenant_id": str(tenant_id),
                "agent_slug": agent_slug,
                "layer": layer,
                "detail": json.dumps(detail, sort_keys=True),
            },
        )
