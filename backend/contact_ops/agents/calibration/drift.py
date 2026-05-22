"""Compute PSI + KS-test drift between rolling 7d and 30d windows.

The math is already in ``contact_ops.agents.trust`` — this module is
the runtime that pulls per-(agent × tenant) approval-rate histories
from ``action_event``, calls the math, and updates
``agent_trust.drift_status`` + ``psi_score`` + ``ks_p_value``.

Phase 3 Design §5: three demotion triggers:

  1. PSI >= 0.20 between 7d and 30d distributions.
  2. Rolling 7d approval rate drops > 15pp below 30d baseline.
  3. Three consecutive daily warning days (PSI in [0.10, 0.20)).

Trigger #3 is tracked via the ``warning_streak`` counter on the
trust row (incremented on warning, reset on stable, demotes when it
hits 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents.trust import (
    classify_drift,
    ks_test_p_value,
    population_stability_index,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DriftResult:
    agent_slug: str
    tenant_id: UUID
    visibility: str
    psi: float
    ks_p_value: float
    drift_status: str  # 'stable' | 'warning' | 'drift'
    warning_streak: int


async def evaluate_drift(
    *,
    db: AsyncSession,
    agent_slug: str,
    tenant_id: UUID,
    visibility: str,
    now: datetime | None = None,
) -> DriftResult:
    """Compute PSI/KS for one (agent × tenant × visibility) row.

    Returns the new drift state. The caller is responsible for writing
    it back to ``agent_trust`` (paired with the posterior update).
    """
    now = now or datetime.now(UTC)
    seven_d_ago = now - timedelta(days=7)
    thirty_d_ago = now - timedelta(days=30)

    # Compute one approval-rate per day for the last 30 days.
    result = await db.execute(
        text(
            """
            WITH days AS (
                SELECT generate_series(
                    date_trunc('day', CAST(:thirty_d_ago AS timestamptz)),
                    date_trunc('day', CAST(:now AS timestamptz)),
                    interval '1 day'
                ) AS day
            ), daily AS (
                SELECT
                    date_trunc('day', proposed_at) AS day,
                    COUNT(*) FILTER (
                        WHERE status IN ('approved', 'applied')
                    ) AS approved,
                    COUNT(*) FILTER (
                        WHERE status IN ('approved', 'applied', 'rejected', 'reverted')
                    ) AS total
                FROM action_event
                WHERE actor->>'sub' = :slug
                  AND tenant_id = CAST(:tenant AS uuid)
                  AND COALESCE(decision_payload->>'visibility', 'private') = :visibility
                  AND proposed_at >= :thirty_d_ago
                GROUP BY 1
            )
            SELECT
                days.day,
                COALESCE(daily.approved, 0) AS approved,
                COALESCE(daily.total, 0) AS total
            FROM days
            LEFT JOIN daily USING (day)
            ORDER BY days.day
            """
        ),
        {
            "slug": agent_slug,
            "tenant": str(tenant_id),
            "visibility": visibility,
            "now": now,
            "thirty_d_ago": thirty_d_ago,
        },
    )

    rows = result.mappings().all()
    seven_d_rates: list[float] = []
    thirty_d_rates: list[float] = []
    for r in rows:
        approved = int(r["approved"])
        total = int(r["total"])
        rate = approved / total if total > 0 else 1.0
        thirty_d_rates.append(rate)
        if r["day"] >= seven_d_ago:
            seven_d_rates.append(rate)

    # Need at least one day of data in each window; otherwise stable by
    # default (the calibration daemon has nothing to drift from yet).
    if not seven_d_rates or not thirty_d_rates:
        return DriftResult(
            agent_slug=agent_slug,
            tenant_id=tenant_id,
            visibility=visibility,
            psi=0.0,
            ks_p_value=1.0,
            drift_status="stable",
            warning_streak=0,
        )

    psi = population_stability_index(thirty_d_rates, seven_d_rates)
    p_value = ks_test_p_value(thirty_d_rates, seven_d_rates)
    status = classify_drift(psi)

    # Update warning streak based on transitions.
    streak = await _next_warning_streak(
        db=db,
        agent_slug=agent_slug,
        tenant_id=tenant_id,
        visibility=visibility,
        new_status=status,
    )

    return DriftResult(
        agent_slug=agent_slug,
        tenant_id=tenant_id,
        visibility=visibility,
        psi=psi,
        ks_p_value=p_value,
        drift_status=status,
        warning_streak=streak,
    )


async def write_drift_state(
    *,
    db: AsyncSession,
    drift: DriftResult,
) -> None:
    """Persist drift status + PSI + KS p-value onto agent_trust."""
    await db.execute(
        text(
            """
            UPDATE agent_trust
            SET psi_score = :psi,
                ks_p_value = :ks,
                drift_status = :status,
                last_updated = now()
            WHERE agent_slug = :slug
              AND tenant_id = CAST(:tenant AS uuid)
              AND visibility = :visibility
            """
        ),
        {
            "slug": drift.agent_slug,
            "tenant": str(drift.tenant_id),
            "visibility": drift.visibility,
            "psi": drift.psi,
            "ks": drift.ks_p_value,
            "status": drift.drift_status,
        },
    )


async def _next_warning_streak(
    *,
    db: AsyncSession,
    agent_slug: str,
    tenant_id: UUID,
    visibility: str,
    new_status: str,
) -> int:
    """Compute next warning_streak counter based on prior drift_status.

    Stored in ``agent_trust.psi_score`` companion column ``warning_streak``
    in migration 0025. The math is:
      stable -> reset to 0
      warning -> previous + 1
      drift -> reset (demotion fires elsewhere)
    """
    if new_status != "warning":
        return 0

    result = await db.execute(
        text(
            """
            SELECT warning_streak FROM agent_trust
            WHERE agent_slug = :slug
              AND tenant_id = CAST(:tenant AS uuid)
              AND visibility = :visibility
            """
        ),
        {
            "slug": agent_slug,
            "tenant": str(tenant_id),
            "visibility": visibility,
        },
    )
    row = result.mappings().first()
    if row is None:
        return 1
    return int(row["warning_streak"] or 0) + 1


async def persist_warning_streak(
    *,
    db: AsyncSession,
    agent_slug: str,
    tenant_id: UUID,
    visibility: str,
    streak: int,
) -> None:
    await db.execute(
        text(
            """
            UPDATE agent_trust
            SET warning_streak = :streak
            WHERE agent_slug = :slug
              AND tenant_id = CAST(:tenant AS uuid)
              AND visibility = :visibility
            """
        ),
        {
            "slug": agent_slug,
            "tenant": str(tenant_id),
            "visibility": visibility,
            "streak": streak,
        },
    )
