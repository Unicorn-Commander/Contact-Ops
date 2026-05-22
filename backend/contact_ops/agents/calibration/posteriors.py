"""Walk ``action_event`` rows; update Beta(α, β) per agent_trust row.

The single piece that turns the trust-ladder math (already in
``contact_ops.agents.trust``) into a live signal. Run daily by the
CalibrationDaemon.

Algorithm:

* For every (agent_slug × tenant_id × visibility) seen in the window,
  bucket the proposals by outcome:
    approved          -> α + 1
    reverted          -> β + 1
    rejected          -> β + 1
    snoozed / dismiss -> neutral
* UPSERT the result into ``agent_trust``. Bootstrap row at
  ``Beta(1, 1)`` and ``initial_trust_tier`` if it didn't exist.
* Recompute ``mean`` (column is GENERATED), ``lower_ci_*``, sample
  counts (total / 7d / 30d), and the rolling approval rates.

The window is bounded by ``calibration_run_log.last_run_at`` — we never
walk action_events older than the previous run, so cost stays
linear-in-new-events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents.trust import (
    BetaPosterior,
    TrustTier,
    tier_from_posterior,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TrustUpdate:
    """One agent_trust row after a calibration pass."""

    agent_slug: str
    tenant_id: UUID
    visibility: str
    alpha: float
    beta: float
    samples_total: int
    samples_7d: int
    samples_30d: int
    approval_rate_7d: float
    approval_rate_30d: float
    posterior_tier: TrustTier


async def update_posteriors_since(
    *,
    db: AsyncSession,
    since: datetime,
    now: datetime | None = None,
) -> list[TrustUpdate]:
    """Walk action_event rows newer than ``since``; update agent_trust.

    Returns the list of (agent × tenant × visibility) updates applied.
    Does NOT change ``current_tier`` — that's the job of
    ``tier_changes.evaluate_promotions_and_demotions``, which reads the
    posterior-implied tier this function computes and decides whether
    to write a tier-change proposal.
    """
    now = now or datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    # Bucket every relevant action_event into one update per
    # (agent, tenant, visibility). Treat ``actor->>'sub'`` as the agent
    # slug for agent-authored events; human-authored events are skipped.
    #
    # Two different windows in play:
    #
    # * **Walk window** (``proposed_at > :since``): only the events
    #   NEWER than the last daemon run drive the incremental Beta α/β
    #   bump. This is what makes the daemon cheap on big tables.
    # * **Rolling 7d / 30d windows**: independently re-counted every
    #   pass for drift detection. These are NOT bounded by ``:since``
    #   because we need the full 30d picture (not just the delta).
    result = await db.execute(
        text(
            """
            WITH walk AS (
                SELECT
                    actor->>'sub' AS agent_slug,
                    tenant_id,
                    COALESCE(
                        decision_payload->>'visibility',
                        'private'
                    ) AS visibility,
                    COUNT(*) FILTER (
                        WHERE status IN ('approved', 'applied')
                    ) AS approved_walk,
                    COUNT(*) FILTER (
                        WHERE status IN ('rejected', 'reverted')
                    ) AS rejected_walk
                FROM action_event
                WHERE actor_type = 'agent'
                  AND proposed_at > :since
                  AND status IN ('approved', 'applied', 'rejected', 'reverted')
                GROUP BY 1, 2, 3
            ),
            rolling AS (
                SELECT
                    actor->>'sub' AS agent_slug,
                    tenant_id,
                    COALESCE(
                        decision_payload->>'visibility',
                        'private'
                    ) AS visibility,
                    COUNT(*) FILTER (
                        WHERE proposed_at >= :seven_days_ago
                    ) AS samples_7d,
                    COUNT(*) FILTER (
                        WHERE proposed_at >= :thirty_days_ago
                    ) AS samples_30d,
                    COUNT(*) FILTER (
                        WHERE proposed_at >= :seven_days_ago
                          AND status IN ('approved', 'applied')
                    ) AS approved_7d,
                    COUNT(*) FILTER (
                        WHERE proposed_at >= :thirty_days_ago
                          AND status IN ('approved', 'applied')
                    ) AS approved_30d
                FROM action_event
                WHERE actor_type = 'agent'
                  AND proposed_at >= :thirty_days_ago
                  AND status IN ('approved', 'applied', 'rejected', 'reverted')
                GROUP BY 1, 2, 3
            )
            SELECT
                COALESCE(walk.agent_slug, rolling.agent_slug) AS agent_slug,
                COALESCE(walk.tenant_id, rolling.tenant_id) AS tenant_id,
                COALESCE(walk.visibility, rolling.visibility) AS visibility,
                COALESCE(walk.approved_walk, 0) AS approved_total,
                COALESCE(walk.rejected_walk, 0) AS rejected_total,
                COALESCE(rolling.samples_7d, 0) AS samples_7d,
                COALESCE(rolling.samples_30d, 0) AS samples_30d,
                COALESCE(rolling.approved_7d, 0) AS approved_7d,
                COALESCE(rolling.approved_30d, 0) AS approved_30d
            FROM walk
            FULL OUTER JOIN rolling USING (agent_slug, tenant_id, visibility)
            """
        ),
        {
            "since": since,
            "seven_days_ago": seven_days_ago,
            "thirty_days_ago": thirty_days_ago,
        },
    )

    updates: list[TrustUpdate] = []
    for row in result.mappings().all():
        agent_slug = str(row["agent_slug"])
        tenant_id = UUID(str(row["tenant_id"]))
        visibility = str(row["visibility"])
        approved_total = int(row["approved_total"])
        rejected_total = int(row["rejected_total"])

        # If the walk window produced no new events for this row, skip
        # the UPSERT entirely — the rolling 7d/30d snapshot will be
        # refreshed on the next pass that does see new events. This
        # keeps ``posteriors_updated`` an honest count of rows where
        # Beta(α, β) actually changed.
        if approved_total == 0 and rejected_total == 0:
            continue

        # Read existing posterior (or default to Beta(1,1) if not yet there).
        existing = await _existing_trust(
            db=db,
            agent_slug=agent_slug,
            tenant_id=tenant_id,
            visibility=visibility,
        )

        new_alpha = float(existing.alpha if existing else 1.0) + approved_total
        new_beta = float(existing.beta if existing else 1.0) + rejected_total

        posterior = BetaPosterior(alpha=new_alpha, beta=new_beta)
        posterior_tier = tier_from_posterior(posterior)

        samples_7d = int(row["samples_7d"])
        samples_30d = int(row["samples_30d"])
        approved_7d = int(row["approved_7d"])
        approved_30d = int(row["approved_30d"])
        approval_rate_7d = approved_7d / max(samples_7d, 1) if samples_7d else 1.0
        approval_rate_30d = approved_30d / max(samples_30d, 1) if samples_30d else 1.0

        # UPSERT — preserve current_tier from the existing row (the daemon's
        # tier_changes module is the only thing that mutates current_tier).
        # Drift status defaults to 'stable'; drift.py overwrites if it fires.
        await db.execute(
            text(
                """
                INSERT INTO agent_trust (
                    agent_slug, tenant_id, visibility,
                    alpha, beta,
                    lower_ci_90, lower_ci_95,
                    samples_total, samples_7d, samples_30d,
                    approval_rate_7d, approval_rate_30d,
                    current_tier, last_updated
                ) VALUES (
                    :slug, CAST(:tenant AS uuid), :visibility,
                    :alpha, :beta,
                    :lower_ci_90, :lower_ci_95,
                    :samples_total, :samples_7d, :samples_30d,
                    :approval_rate_7d, :approval_rate_30d,
                    :initial_tier, now()
                )
                ON CONFLICT (agent_slug, tenant_id, visibility) DO UPDATE
                SET alpha = EXCLUDED.alpha,
                    beta = EXCLUDED.beta,
                    lower_ci_90 = EXCLUDED.lower_ci_90,
                    lower_ci_95 = EXCLUDED.lower_ci_95,
                    samples_total = agent_trust.samples_total
                        + (EXCLUDED.samples_total - agent_trust.samples_total),
                    samples_7d = EXCLUDED.samples_7d,
                    samples_30d = EXCLUDED.samples_30d,
                    approval_rate_7d = EXCLUDED.approval_rate_7d,
                    approval_rate_30d = EXCLUDED.approval_rate_30d,
                    last_updated = now()
                """
            ),
            {
                "slug": agent_slug,
                "tenant": str(tenant_id),
                "visibility": visibility,
                "alpha": new_alpha,
                "beta": new_beta,
                "lower_ci_90": posterior.lower_credible_interval(0.90),
                "lower_ci_95": posterior.lower_credible_interval(0.95),
                "samples_total": approved_total + rejected_total
                    + (existing.samples_total if existing else 0),
                "samples_7d": samples_7d,
                "samples_30d": samples_30d,
                "approval_rate_7d": approval_rate_7d,
                "approval_rate_30d": approval_rate_30d,
                "initial_tier": int(
                    existing.current_tier if existing else _bootstrap_tier(agent_slug)
                ),
            },
        )

        updates.append(
            TrustUpdate(
                agent_slug=agent_slug,
                tenant_id=tenant_id,
                visibility=visibility,
                alpha=new_alpha,
                beta=new_beta,
                samples_total=int(row["approved_total"]) + int(row["rejected_total"]),
                samples_7d=samples_7d,
                samples_30d=samples_30d,
                approval_rate_7d=approval_rate_7d,
                approval_rate_30d=approval_rate_30d,
                posterior_tier=posterior_tier,
            )
        )

    logger.info(
        "calibration_posteriors_updated",
        rows=len(updates),
        since=since.isoformat(),
    )
    return updates


@dataclass(frozen=True)
class _ExistingTrust:
    alpha: float
    beta: float
    current_tier: int
    samples_total: int


async def _existing_trust(
    *,
    db: AsyncSession,
    agent_slug: str,
    tenant_id: UUID,
    visibility: str,
) -> _ExistingTrust | None:
    result = await db.execute(
        text(
            """
            SELECT alpha, beta, current_tier, samples_total
            FROM agent_trust
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
        return None
    return _ExistingTrust(
        alpha=float(row["alpha"]),
        beta=float(row["beta"]),
        current_tier=int(row["current_tier"]),
        samples_total=int(row["samples_total"]),
    )


def _bootstrap_tier(agent_slug: str) -> int:
    """Bootstrap tier when no agent_trust row exists.

    Reads from the in-process registry. Falls back to T0 if the
    daemon sees an agent slug not registered (which would itself
    indicate a deploy bug — log a warning).
    """
    from contact_ops.agents.registry import get_agent

    a = get_agent(agent_slug)
    if a is None:
        logger.warning("calibration_unknown_agent_slug", slug=agent_slug)
        return int(TrustTier.T0_PROBATION)
    return int(a.initial_trust_tier)
