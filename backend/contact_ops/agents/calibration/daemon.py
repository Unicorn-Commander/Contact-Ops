"""CalibrationDaemon — the BaseAgent that orchestrates the daily walk.

Single ``run_calibration_pass`` entry point that:

  1. Reads ``last_calibration_at`` from ``calibration_run_log``.
  2. Calls ``posteriors.update_posteriors_since`` (UPSERTs agent_trust).
  3. For each updated row, calls ``drift.evaluate_drift`` + writes the
     drift state.
  4. Calls ``tier_changes.evaluate_and_apply_tier_changes`` to emit
     promote/demote ``action_event`` rows.
  5. Stamps the new ``calibration_run_log`` row.

The daemon registers as a BATCH agent with a 03:00 UTC daily cron.
``contact_ops.agents.runtime`` discovers it via ``register_agent`` and
``contact_ops.agents.runtime.celery_app.conf.beat_schedule`` picks up
the trigger automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents import (
    AgentClass,
    AgentContext,
    AgentDef,
    AgentResult,
    BaseAgent,
    TrustTier,
)
from contact_ops.agents.calibration.drift import (
    evaluate_drift,
    persist_warning_streak,
    write_drift_state,
)
from contact_ops.agents.calibration.posteriors import update_posteriors_since
from contact_ops.agents.calibration.tier_changes import (
    TierChange,
    evaluate_and_apply_tier_changes,
)
from contact_ops.agents.registry import register_agent

logger = structlog.get_logger(__name__)


CALIBRATION_DAEMON_DEF = AgentDef(
    slug="calibration-daemon",
    name="Calibration Daemon",
    version="1.0.0",
    agent_class=AgentClass.BATCH,
    description=(
        "Daily walk of action_event; updates agent_trust Beta posteriors, "
        "detects drift via PSI + KS, emits tier_promote/tier_demote events."
    ),
    cost_budget_monthly_cents=500,  # no LLM calls — pure SQL + math
    initial_trust_tier=TrustTier.T2_TRUSTED,  # not human-graded; bootstrap above probation
    triggers=("0 3 * * *",),  # 03:00 UTC daily
    declared_capabilities=("db.query", "db.update_agent_trust"),
)


@dataclass(frozen=True)
class CalibrationPassResult:
    daemon_run_id: UUID
    started_at: datetime
    ended_at: datetime
    posteriors_updated: int
    drifts_evaluated: int
    tier_promotes_proposed: int
    tier_demotes_applied: int
    fleet_revert_rate_pct: float | None


class CalibrationDaemon(BaseAgent):
    """BaseAgent wrapper. ``_run`` delegates to ``run_calibration_pass``."""

    async def _run(self, ctx: AgentContext) -> AgentResult:
        result = await run_calibration_pass(
            db=ctx.db,
            audit_db=ctx.audit_db,
        )
        return AgentResult(
            proposals_emitted=result.tier_promotes_proposed + result.tier_demotes_applied,
            proposals_auto_applied=result.tier_demotes_applied,
            decision_summary=(
                f"calibration: {result.posteriors_updated} posteriors updated, "
                f"{result.tier_promotes_proposed} promotions proposed, "
                f"{result.tier_demotes_applied} demotions applied"
            ),
            extra={
                "daemon_run_id": str(result.daemon_run_id),
                "fleet_revert_rate_pct": result.fleet_revert_rate_pct,
            },
        )


register_agent(CALIBRATION_DAEMON_DEF)


async def run_calibration_pass(
    *,
    db: AsyncSession,
    audit_db: AsyncSession,
    now: datetime | None = None,
    shadow: bool = False,
) -> CalibrationPassResult:
    """Execute one calibration pass; returns aggregate stats.

    Idempotent: a second invocation immediately after the first will
    see the updated ``calibration_run_log.last_run_at`` and walk zero
    action_event rows.

    ``shadow`` (observe-before-enforce): posteriors + drift state are still
    updated (those are observations), but tier changes are only COMPUTED + logged
    as ``calibration_would_*``, never emitted or applied. Used for the dormant /
    shadow rollout of the one path that auto-applies autonomy changes fleet-wide.
    """
    daemon_run_id = uuid4()
    started_at = now or datetime.now(UTC)

    since = await _last_run_at(db=db) or (started_at - timedelta(days=7))

    updates = await update_posteriors_since(db=db, since=since, now=started_at)

    drifts = {}
    for upd in updates:
        drift = await evaluate_drift(
            db=db,
            agent_slug=upd.agent_slug,
            tenant_id=upd.tenant_id,
            visibility=upd.visibility,
            now=started_at,
        )
        await write_drift_state(db=db, drift=drift)
        await persist_warning_streak(
            db=db,
            agent_slug=drift.agent_slug,
            tenant_id=drift.tenant_id,
            visibility=drift.visibility,
            streak=drift.warning_streak,
        )
        drifts[(upd.agent_slug, upd.tenant_id, upd.visibility)] = drift

    changes: list[TierChange] = await evaluate_and_apply_tier_changes(
        db=db,
        audit_db=audit_db,
        updates=updates,
        drifts=drifts,
        daemon_run_id=daemon_run_id,
        shadow=shadow,
    )
    promotes = sum(1 for c in changes if c.kind == "promote")
    demotes = sum(1 for c in changes if c.kind == "demote")

    fleet_revert_rate = await _fleet_revert_rate(db=db, now=started_at)

    ended_at = datetime.now(UTC)
    await _record_run(
        db=db,
        daemon_run_id=daemon_run_id,
        started_at=started_at,
        ended_at=ended_at,
        posteriors_updated=len(updates),
        drifts_evaluated=len(drifts),
        tier_promotes_proposed=promotes,
        tier_demotes_applied=demotes,
        fleet_revert_rate_pct=fleet_revert_rate,
    )
    await db.commit()
    await audit_db.commit()

    logger.info(
        "calibration_pass_complete",
        daemon_run_id=str(daemon_run_id),
        posteriors=len(updates),
        promotes=promotes,
        demotes=demotes,
        fleet_revert_rate_pct=fleet_revert_rate,
        elapsed_seconds=(ended_at - started_at).total_seconds(),
    )

    return CalibrationPassResult(
        daemon_run_id=daemon_run_id,
        started_at=started_at,
        ended_at=ended_at,
        posteriors_updated=len(updates),
        drifts_evaluated=len(drifts),
        tier_promotes_proposed=promotes,
        tier_demotes_applied=demotes,
        fleet_revert_rate_pct=fleet_revert_rate,
    )


async def _last_run_at(*, db: AsyncSession) -> datetime | None:
    result = await db.execute(
        text(
            """
            SELECT ended_at
            FROM calibration_run_log
            ORDER BY ended_at DESC NULLS LAST
            LIMIT 1
            """
        )
    )
    row = result.first()
    if row is None:
        return None
    ended_at = row[0]
    return ended_at if isinstance(ended_at, datetime) else None


async def _fleet_revert_rate(
    *,
    db: AsyncSession,
    now: datetime,
) -> float | None:
    """Rolling-24h fleet-wide revert rate, for the circuit-breaker hint."""
    twenty_four_h_ago = now - timedelta(hours=24)
    result = await db.execute(
        text(
            """
            SELECT
                COUNT(*) FILTER (WHERE status IN ('applied','approved')) AS applied,
                COUNT(*) FILTER (WHERE status = 'reverted') AS reverted
            FROM action_event
            WHERE actor_type = 'agent'
              AND proposed_at >= :since
            """
        ),
        {"since": twenty_four_h_ago},
    )
    row = result.mappings().one()
    applied = int(row["applied"])
    reverted = int(row["reverted"])
    if applied == 0:
        return None
    return (reverted / applied) * 100.0


async def _record_run(
    *,
    db: AsyncSession,
    daemon_run_id: UUID,
    started_at: datetime,
    ended_at: datetime,
    posteriors_updated: int,
    drifts_evaluated: int,
    tier_promotes_proposed: int,
    tier_demotes_applied: int,
    fleet_revert_rate_pct: float | None,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO calibration_run_log (
                id, started_at, ended_at,
                posteriors_updated, drifts_evaluated,
                tier_promotes_proposed, tier_demotes_applied,
                fleet_revert_rate_pct
            ) VALUES (
                CAST(:id AS uuid), :started, :ended,
                :posteriors, :drifts,
                :promotes, :demotes,
                :revert_rate
            )
            """
        ),
        {
            "id": str(daemon_run_id),
            "started": started_at,
            "ended": ended_at,
            "posteriors": posteriors_updated,
            "drifts": drifts_evaluated,
            "promotes": tier_promotes_proposed,
            "demotes": tier_demotes_applied,
            "revert_rate": fleet_revert_rate_pct,
        },
    )
