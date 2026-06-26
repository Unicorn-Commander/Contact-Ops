"""Decide whether to promote, demote, or hold the current tier.

Reads the (already-updated) ``agent_trust`` row and the
``DriftResult``, applies Phase 3 Design §5 rules, and either:

* **Promotes** by emitting an ``action_event`` of type
  ``calibration.tier_promote`` (status=proposed) for Aaron's review.
* **Demotes** by updating ``agent_trust.current_tier`` immediately
  and emitting ``calibration.tier_demote`` (status=applied) with a
  Laminar deep-link payload + Slack-alert hint.
* **Holds** otherwise.

Promotions are opt-in per Aaron's `feedback_quality_standard` — we
never silently slide trust. Demotions are auto-applied because the
default safe direction is "stricter".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents.calibration.drift import DriftResult
from contact_ops.agents.calibration.posteriors import TrustUpdate
from contact_ops.agents.trust import (
    TrustTier,
    should_demote,
    tier_name,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TierChange:
    agent_slug: str
    tenant_id: UUID
    visibility: str
    from_tier: TrustTier
    to_tier: TrustTier
    kind: str  # 'promote' | 'demote'
    action_event_id: UUID | None  # None in shadow mode (nothing was emitted)
    rationale: str


async def evaluate_and_apply_tier_changes(
    *,
    db: AsyncSession,
    audit_db: AsyncSession,
    updates: list[TrustUpdate],
    drifts: dict[tuple[str, UUID, str], DriftResult],
    daemon_run_id: UUID,
    shadow: bool = False,
) -> list[TierChange]:
    """For each updated trust row, decide and apply tier changes.

    Returns the list of changes (proposals + auto-applied demotions)
    so the daemon can summarize them in its log + Slack alert.

    When ``shadow`` is True the SAME promote/demote decisions are computed and
    returned (with ``action_event_id=None``) and logged as
    ``calibration_would_demote`` / ``calibration_would_promote``, but NOTHING is
    emitted or applied: no ``action_event`` is written and no
    ``agent_trust.current_tier`` is changed. This is the observe-before-enforce
    safety for the one path that auto-applies autonomy changes fleet-wide.
    """
    changes: list[TierChange] = []
    for upd in updates:
        current_tier = await _current_tier(
            db=db,
            agent_slug=upd.agent_slug,
            tenant_id=upd.tenant_id,
            visibility=upd.visibility,
        )
        if current_tier is None:
            continue

        drift = drifts.get((upd.agent_slug, upd.tenant_id, upd.visibility))

        # Demotion check first — always safe.
        demote = should_demote(
            current_tier=current_tier,
            rolling_7d_mean=upd.approval_rate_7d,
            rolling_30d_mean=upd.approval_rate_30d,
            psi=drift.psi if drift else 0.0,
            consecutive_warning_days=drift.warning_streak if drift else 0,
        )
        if demote:
            new_tier = TrustTier(max(int(current_tier) - 1, int(TrustTier.T0_PROBATION)))
            rationale = _demotion_rationale(
                current_tier=current_tier,
                drift=drift,
                upd=upd,
            )
            event_id: UUID | None = None
            if shadow:
                logger.info(
                    "calibration_would_demote",
                    agent_slug=upd.agent_slug,
                    tenant_id=str(upd.tenant_id),
                    visibility=upd.visibility,
                    from_tier=int(current_tier),
                    to_tier=int(new_tier),
                    rationale=rationale,
                )
            else:
                event_id = await _emit_tier_change(
                    audit_db=audit_db,
                    agent_slug=upd.agent_slug,
                    tenant_id=upd.tenant_id,
                    visibility=upd.visibility,
                    from_tier=current_tier,
                    to_tier=new_tier,
                    kind="demote",
                    rationale=rationale,
                    daemon_run_id=daemon_run_id,
                    auto_apply=True,
                )
                await _apply_tier(
                    db=db,
                    agent_slug=upd.agent_slug,
                    tenant_id=upd.tenant_id,
                    visibility=upd.visibility,
                    new_tier=new_tier,
                    is_demotion=True,
                )
            changes.append(
                TierChange(
                    agent_slug=upd.agent_slug,
                    tenant_id=upd.tenant_id,
                    visibility=upd.visibility,
                    from_tier=current_tier,
                    to_tier=new_tier,
                    kind="demote",
                    action_event_id=event_id,
                    rationale=rationale,
                )
            )
            continue

        # Promotion check.
        if upd.posterior_tier > current_tier:
            new_tier = TrustTier(min(int(current_tier) + 1, int(upd.posterior_tier)))
            rationale = _promotion_rationale(
                current_tier=current_tier,
                new_tier=new_tier,
                upd=upd,
                drift=drift,
            )
            promote_event_id: UUID | None = None
            if shadow:
                logger.info(
                    "calibration_would_promote",
                    agent_slug=upd.agent_slug,
                    tenant_id=str(upd.tenant_id),
                    visibility=upd.visibility,
                    from_tier=int(current_tier),
                    to_tier=int(new_tier),
                    rationale=rationale,
                )
            else:
                promote_event_id = await _emit_tier_change(
                    audit_db=audit_db,
                    agent_slug=upd.agent_slug,
                    tenant_id=upd.tenant_id,
                    visibility=upd.visibility,
                    from_tier=current_tier,
                    to_tier=new_tier,
                    kind="promote",
                    rationale=rationale,
                    daemon_run_id=daemon_run_id,
                    auto_apply=False,
                )
            changes.append(
                TierChange(
                    agent_slug=upd.agent_slug,
                    tenant_id=upd.tenant_id,
                    visibility=upd.visibility,
                    from_tier=current_tier,
                    to_tier=new_tier,
                    kind="promote",
                    action_event_id=promote_event_id,
                    rationale=rationale,
                )
            )

    return changes


async def apply_promotion(
    *,
    db: AsyncSession,
    audit_db: AsyncSession,
    proposal_event_id: UUID,
) -> None:
    """Effect a promoted proposal by bumping ``agent_trust.current_tier``.

    Called by the inbox's ``approve_proposal`` handler when it sees an
    event_type ``calibration.tier_promote``. The MCP-tool-level glue
    routes here.
    """
    result = await audit_db.execute(
        text(
            """
            SELECT decision_payload
            FROM action_event
            WHERE event_id = CAST(:id AS uuid)
              AND event_type = 'calibration.tier_promote'
            """
        ),
        {"id": str(proposal_event_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise ValueError(f"no promote proposal {proposal_event_id}")
    payload = row["decision_payload"] or {}
    if isinstance(payload, str):
        payload = json.loads(payload)

    target = payload.get("payload_after", {})
    await _apply_tier(
        db=db,
        agent_slug=str(target["agent_slug"]),
        tenant_id=UUID(str(target["tenant_id"])),
        visibility=str(target["visibility"]),
        new_tier=TrustTier(int(target["to_tier"])),
        is_demotion=False,
    )


# ---- internals ----

async def _current_tier(
    *,
    db: AsyncSession,
    agent_slug: str,
    tenant_id: UUID,
    visibility: str,
) -> TrustTier | None:
    result = await db.execute(
        text(
            """
            SELECT current_tier FROM agent_trust
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
    return TrustTier(int(row["current_tier"]))


async def _apply_tier(
    *,
    db: AsyncSession,
    agent_slug: str,
    tenant_id: UUID,
    visibility: str,
    new_tier: TrustTier,
    is_demotion: bool,
) -> None:
    if is_demotion:
        await db.execute(
            text(
                """
                UPDATE agent_trust
                SET current_tier = :tier,
                    last_demotion_at = now(),
                    last_updated = now()
                WHERE agent_slug = :slug
                  AND tenant_id = CAST(:tenant AS uuid)
                  AND visibility = :visibility
                """
            ),
            {
                "slug": agent_slug,
                "tenant": str(tenant_id),
                "visibility": visibility,
                "tier": int(new_tier),
            },
        )
    else:
        await db.execute(
            text(
                """
                UPDATE agent_trust
                SET current_tier = :tier,
                    last_updated = now()
                WHERE agent_slug = :slug
                  AND tenant_id = CAST(:tenant AS uuid)
                  AND visibility = :visibility
                """
            ),
            {
                "slug": agent_slug,
                "tenant": str(tenant_id),
                "visibility": visibility,
                "tier": int(new_tier),
            },
        )


async def _emit_tier_change(
    *,
    audit_db: AsyncSession,
    agent_slug: str,
    tenant_id: UUID,
    visibility: str,
    from_tier: TrustTier,
    to_tier: TrustTier,
    kind: str,  # 'promote' | 'demote'
    rationale: str,
    daemon_run_id: UUID,
    auto_apply: bool,
) -> UUID:
    """Write an action_event row recording the tier change.

    Promotions land as ``status='proposed'`` for Aaron's review.
    Demotions land as ``status='applied'`` with applied_at=now() because
    we've already mutated agent_trust.current_tier alongside this write.
    """
    event_type = f"calibration.tier_{kind}"
    payload_before = {"current_tier": int(from_tier)}
    payload_after = {
        "agent_slug": agent_slug,
        "tenant_id": str(tenant_id),
        "visibility": visibility,
        "from_tier": int(from_tier),
        "to_tier": int(to_tier),
    }
    content_hash = hashlib.sha256(
        json.dumps(
            {"agent": agent_slug, "kind": kind, "from": int(from_tier), "to": int(to_tier)},
            sort_keys=True,
        ).encode("utf-8")
    ).digest()
    idempotency_key = hashlib.sha256(
        f"calibration-daemon|{daemon_run_id}|{agent_slug}|{tenant_id}|{visibility}|{kind}".encode()
    ).hexdigest()

    result = await audit_db.execute(
        text(
            """
            INSERT INTO action_event (
                event_type, event_version, tenant_id,
                aggregate_type, aggregate_id,
                payload, actor, actor_type,
                confidence, evidence, rationale,
                status, applied_at, content_hash,
                idempotency_key, decision_payload, reversibility_class,
                agent_version, trust_tier_at_creation, triggered_by
            ) VALUES (
                :event_type, 1, CAST(:tenant AS uuid),
                CAST('person' AS entity_kind), gen_random_uuid(),
                CAST(:payload AS jsonb),
                CAST(:actor AS jsonb), 'agent'::actor_type,
                1.0, CAST(:evidence AS jsonb), :rationale,
                CAST(:status AS event_status),
                CASE WHEN :auto_apply THEN now() ELSE NULL END,
                :content_hash,
                :idempotency_key, CAST(:decision_payload AS jsonb), :reversibility,
                'calibration-daemon-1.0', :tier, 'cron:0 3 * * *'
            )
            ON CONFLICT (idempotency_key) DO UPDATE
                SET status = EXCLUDED.status
            RETURNING event_id
            """
        ),
        {
            "event_type": event_type,
            "tenant": str(tenant_id),
            "payload": json.dumps(payload_after, sort_keys=True),
            "actor": json.dumps(
                {
                    "sub": "calibration-daemon",
                    "agent_version": "1.0",
                    "act": {"sub": "system"},
                },
                sort_keys=True,
            ),
            "evidence": json.dumps(
                {
                    "daemon_run_id": str(daemon_run_id),
                    "payload_before": payload_before,
                },
                sort_keys=True,
            ),
            "rationale": rationale[:8192],
            "status": "applied" if auto_apply else "proposed",
            "auto_apply": auto_apply,
            "content_hash": content_hash,
            "idempotency_key": idempotency_key,
            "decision_payload": json.dumps(
                {
                    "payload_before": payload_before,
                    "payload_after": payload_after,
                    "auto_apply_eligible": auto_apply,
                    "reversibility": "reversible",
                    "tier_at_creation": int(to_tier if auto_apply else from_tier),
                    "agent_slug": agent_slug,
                    "tenant_id": str(tenant_id),
                    "visibility": visibility,
                    "to_tier": int(to_tier),
                },
                sort_keys=True,
            ),
            "reversibility": "reversible",
            "tier": int(to_tier if auto_apply else from_tier),
        },
    )
    event_id = UUID(str(result.scalar_one()))
    logger.info(
        "calibration_tier_change_emitted",
        kind=kind,
        agent=agent_slug,
        tenant=str(tenant_id),
        from_tier=int(from_tier),
        to_tier=int(to_tier),
        event_id=str(event_id),
    )
    return event_id


def _promotion_rationale(
    *,
    current_tier: TrustTier,
    new_tier: TrustTier,
    upd: TrustUpdate,
    drift: DriftResult | None,
) -> str:
    return (
        f"{upd.agent_slug} hit {tier_name(new_tier)} threshold "
        f"(was {tier_name(current_tier)}): "
        f"Beta(α={upd.alpha:.1f}, β={upd.beta:.1f}) "
        f"mean={upd.alpha/(upd.alpha+upd.beta):.3f}, "
        f"samples={upd.samples_total}, "
        f"7d-approval={upd.approval_rate_7d:.2%}, "
        f"drift={drift.drift_status if drift else 'unknown'}. "
        f"Approve in /inbox to enable {tier_name(new_tier)} auto-apply."
    )


def _demotion_rationale(
    *,
    current_tier: TrustTier,
    drift: DriftResult | None,
    upd: TrustUpdate,
) -> str:
    triggers = []
    if drift and drift.psi >= 0.20:
        triggers.append(f"PSI {drift.psi:.2f} >= 0.20")
    if upd.approval_rate_30d - upd.approval_rate_7d > 0.15:
        triggers.append(
            f"7d approval ({upd.approval_rate_7d:.2%}) dropped > 15pp "
            f"below 30d baseline ({upd.approval_rate_30d:.2%})"
        )
    if drift and drift.warning_streak >= 3:
        triggers.append("3 consecutive PSI>=0.10 warning days")
    return (
        f"{upd.agent_slug} demoted from {tier_name(current_tier)}: "
        + ("; ".join(triggers) if triggers else "drift detected")
        + ". Auto-applied. Check /inbox for the proposals it rejected."
    )
