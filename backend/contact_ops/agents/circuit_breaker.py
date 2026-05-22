"""Circuit breaker (per-agent + fleet-wide).

Per Phase 3 Design §7. Two scopes:

* **Per-agent.** When ``revert_rate_24h > 0.50`` AND ``total_outcomes > 20``
  over a 1-hour window, that agent pauses. Resume via the
  ``resume_agent`` MCP admin tool.

* **Fleet-wide.** When ``revert_rate_24h > 0.10`` across ALL applied
  proposals in the last 1 hour, the entire auto-apply rail pauses and
  every new proposal routes to manual review. Aaron's explicit ack
  resumes it.

State lives in Redis with a 1-hour TTL so a forgotten breaker un-trips
automatically. Each open/close transition writes to
``circuit_breaker_ack`` for the audit trail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents.errors import CircuitBreakerOpenError

logger = structlog.get_logger(__name__)

REVERT_RATE_AGENT_THRESHOLD = 0.50
REVERT_RATE_FLEET_THRESHOLD = 0.10
MIN_OUTCOMES_PER_AGENT = 20
WINDOW_HOURS = 1
BREAKER_TTL_SECONDS = 3600

_AGENT_KEY_FMT = "contactops:cb:agent:{slug}"
_FLEET_KEY = "contactops:cb:fleet"


@dataclass(frozen=True)
class BreakerState:
    scope: str  # "agent:<slug>" | "fleet"
    open: bool
    opened_at: datetime | None
    reason: str | None


class CircuitBreaker:
    """Redis-backed circuit breaker with Postgres audit trail."""

    def __init__(self, *, redis: Redis, db: AsyncSession) -> None:
        self.redis = redis
        self.db = db

    async def check(self, *, agent_slug: str) -> None:
        """Raise ``CircuitBreakerOpenError`` if the agent or fleet is paused."""
        fleet_state = await self._get_state(_FLEET_KEY)
        if fleet_state is not None and fleet_state.open:
            raise CircuitBreakerOpenError(
                scope="fleet",
                reason=fleet_state.reason or "fleet auto-apply paused",
            )
        agent_state = await self._get_state(_AGENT_KEY_FMT.format(slug=agent_slug))
        if agent_state is not None and agent_state.open:
            raise CircuitBreakerOpenError(
                scope=f"agent:{agent_slug}",
                reason=agent_state.reason or "agent paused",
            )

    async def evaluate(self, *, agent_slug: str) -> BreakerState | None:
        """Recompute the per-agent revert rate; open if threshold crossed.

        Called by the Calibration Daemon every minute and by ``BaseAgent``
        after any newly recorded revert. Returns the state that *changed*
        or None if nothing changed.
        """
        applied, reverted = await self._counts(
            agent_slug=agent_slug, window_hours=WINDOW_HOURS
        )
        if applied < MIN_OUTCOMES_PER_AGENT:
            return None
        revert_rate = reverted / max(applied, 1)
        if revert_rate >= REVERT_RATE_AGENT_THRESHOLD:
            reason = (
                f"per-agent revert rate {revert_rate:.2%} >= "
                f"{REVERT_RATE_AGENT_THRESHOLD:.0%} "
                f"({reverted}/{applied} in last {WINDOW_HOURS}h)"
            )
            return await self._open(
                key=_AGENT_KEY_FMT.format(slug=agent_slug),
                scope=f"agent:{agent_slug}",
                reason=reason,
                agent_slug=agent_slug,
            )
        return None

    async def evaluate_fleet(self) -> BreakerState | None:
        """Recompute the fleet-wide revert rate; open if threshold crossed."""
        applied, reverted = await self._counts(
            agent_slug=None, window_hours=WINDOW_HOURS
        )
        if applied == 0:
            return None
        revert_rate = reverted / applied
        if revert_rate >= REVERT_RATE_FLEET_THRESHOLD:
            reason = (
                f"fleet-wide revert rate {revert_rate:.2%} >= "
                f"{REVERT_RATE_FLEET_THRESHOLD:.0%} "
                f"({reverted}/{applied} in last {WINDOW_HOURS}h)"
            )
            return await self._open(
                key=_FLEET_KEY,
                scope="fleet",
                reason=reason,
                agent_slug=None,
            )
        return None

    async def pause(
        self,
        *,
        agent_slug: str,
        actor_user_id: UUID,
        reason: str,
    ) -> BreakerState:
        """Manually pause an agent (called by admin MCP tool)."""
        return await self._open(
            key=_AGENT_KEY_FMT.format(slug=agent_slug),
            scope=f"agent:{agent_slug}",
            reason=reason,
            agent_slug=agent_slug,
            actor_user_id=actor_user_id,
        )

    async def resume(
        self,
        *,
        agent_slug: str | None,
        actor_user_id: UUID,
        reason: str,
    ) -> BreakerState:
        """Resume an agent (or the fleet) and write an ack row."""
        if agent_slug is None:
            key = _FLEET_KEY
            scope = "fleet"
        else:
            key = _AGENT_KEY_FMT.format(slug=agent_slug)
            scope = f"agent:{agent_slug}"
        await self.redis.delete(key)
        await self._ack(
            scope=scope,
            agent_slug=agent_slug,
            transition="resumed",
            reason=reason,
            actor_user_id=actor_user_id,
        )
        return BreakerState(scope=scope, open=False, opened_at=None, reason=None)

    async def _open(
        self,
        *,
        key: str,
        scope: str,
        reason: str,
        agent_slug: str | None,
        actor_user_id: UUID | None = None,
    ) -> BreakerState:
        opened_at = datetime.now(UTC)
        payload = {
            "open": True,
            "opened_at": opened_at.isoformat(),
            "reason": reason,
        }
        await self.redis.set(key, json.dumps(payload), ex=BREAKER_TTL_SECONDS)
        await self._ack(
            scope=scope,
            agent_slug=agent_slug,
            transition="opened",
            reason=reason,
            actor_user_id=actor_user_id,
        )
        logger.warning("circuit_breaker_open", scope=scope, reason=reason)
        return BreakerState(
            scope=scope,
            open=True,
            opened_at=opened_at,
            reason=reason,
        )

    async def _get_state(self, key: str) -> BreakerState | None:
        raw = await self.redis.get(key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw if isinstance(raw, str) else raw.decode())
        except (ValueError, AttributeError):
            return None
        scope = key.removeprefix("contactops:cb:")
        return BreakerState(
            scope=scope,
            open=bool(payload.get("open", False)),
            opened_at=(
                datetime.fromisoformat(payload["opened_at"])
                if payload.get("opened_at")
                else None
            ),
            reason=payload.get("reason"),
        )

    async def _counts(
        self,
        *,
        agent_slug: str | None,
        window_hours: int,
    ) -> tuple[int, int]:
        """Return (applied_count, reverted_count) for the window."""
        if agent_slug is None:
            stmt = text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'applied') AS applied,
                    COUNT(*) FILTER (WHERE status = 'reverted') AS reverted
                FROM action_event
                WHERE proposed_at >= now() - make_interval(hours => :hours)
                """
            )
            params: dict[str, Any] = {"hours": window_hours}
        else:
            stmt = text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'applied') AS applied,
                    COUNT(*) FILTER (WHERE status = 'reverted') AS reverted
                FROM action_event
                WHERE proposed_at >= now() - make_interval(hours => :hours)
                  AND (actor ->> 'sub') = :agent_slug
                """
            )
            params = {"hours": window_hours, "agent_slug": agent_slug}
        result = await self.db.execute(stmt, params)
        row = result.mappings().one()
        return int(row["applied"]), int(row["reverted"])

    async def _ack(
        self,
        *,
        scope: str,
        agent_slug: str | None,
        transition: str,
        reason: str,
        actor_user_id: UUID | None,
    ) -> None:
        await self.db.execute(
            text(
                """
                INSERT INTO circuit_breaker_ack (
                    scope, agent_slug, transition, reason, actor_user_id,
                    occurred_at
                ) VALUES (
                    :scope, :agent_slug, :transition, :reason,
                    CAST(:actor AS uuid), now()
                )
                """
            ),
            {
                "scope": scope,
                "agent_slug": agent_slug,
                "transition": transition,
                "reason": reason[:1024],
                "actor": str(actor_user_id) if actor_user_id else None,
            },
        )
