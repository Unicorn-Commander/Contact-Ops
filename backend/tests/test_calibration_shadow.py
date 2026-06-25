"""Calibration shadow mode: tier changes are computed + logged, not applied.

evaluate_and_apply_tier_changes is the one path that auto-applies tier
DEMOTIONS fleet-wide (UPDATE agent_trust.current_tier + an applied
action_event). CALIBRATION_SHADOW gates that: in shadow the SAME demote/promote
decisions are returned (with action_event_id=None) and logged, but no
action_event is emitted and no current_tier is changed. These tests pin that
contract so the observe-before-enforce safety cannot silently regress.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from contact_ops.agents.calibration.drift import DriftResult
from contact_ops.agents.calibration.posteriors import TrustUpdate
from contact_ops.agents.calibration.tier_changes import (
    evaluate_and_apply_tier_changes,
)
from contact_ops.agents.trust import TrustTier

_VIS = "private"


async def _seed_trust(db, slug: str, tid, tier: TrustTier) -> None:
    await db.execute(
        text(
            """
            INSERT INTO agent_trust (agent_slug, tenant_id, visibility,
                                     current_tier, alpha, beta)
            VALUES (:s, :t, :v, :tier, 1.0, 1.0)
            """
        ),
        {"s": slug, "t": tid, "v": _VIS, "tier": int(tier)},
    )


def _demoting_update(slug: str, tid) -> TrustUpdate:
    # posterior_tier is low so the promote branch never fires; the demote branch
    # fires off the drift below.
    return TrustUpdate(
        agent_slug=slug,
        tenant_id=tid,
        visibility=_VIS,
        alpha=1.0,
        beta=1.0,
        samples_total=100,
        samples_7d=20,
        samples_30d=80,
        approval_rate_7d=0.5,
        approval_rate_30d=0.9,
        posterior_tier=TrustTier.T0_PROBATION,
    )


def _demoting_drift(slug: str, tid) -> DriftResult:
    # psi >= 0.2 is the first should_demote trigger.
    return DriftResult(
        agent_slug=slug,
        tenant_id=tid,
        visibility=_VIS,
        psi=0.3,
        ks_p_value=0.01,
        drift_status="drift",
        warning_streak=3,
    )


async def _current_tier(db, slug: str, tid) -> int | None:
    row = (
        await db.execute(
            text(
                "SELECT current_tier FROM agent_trust "
                "WHERE agent_slug=:s AND tenant_id=:t AND visibility=:v"
            ),
            {"s": slug, "t": tid, "v": _VIS},
        )
    ).first()
    return None if row is None else int(row[0])


async def _demote_event_count(db, tid) -> int:
    return (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM action_event "
                "WHERE tenant_id=:t AND event_type='calibration.tier_demote'"
            ),
            {"t": tid},
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_shadow_computes_but_does_not_apply_or_emit(db_session, seeded_tenants):
    tid = seeded_tenants["non_hipaa"]
    slug = "shadow-agent"
    await _seed_trust(db_session, slug, tid, TrustTier.T2_TRUSTED)
    await db_session.commit()

    changes = await evaluate_and_apply_tier_changes(
        db=db_session,
        audit_db=db_session,
        updates=[_demoting_update(slug, tid)],
        drifts={(slug, tid, _VIS): _demoting_drift(slug, tid)},
        daemon_run_id=uuid.uuid4(),
        shadow=True,
    )

    # The decision is still computed + returned...
    assert len(changes) == 1
    assert changes[0].kind == "demote"
    assert changes[0].to_tier == TrustTier.T1_TRAINEE
    # ...but nothing was emitted or applied.
    assert changes[0].action_event_id is None
    assert await _current_tier(db_session, slug, tid) == int(TrustTier.T2_TRUSTED)
    assert await _demote_event_count(db_session, tid) == 0


@pytest.mark.asyncio
async def test_enforce_applies_demotion_and_emits(db_session, seeded_tenants):
    tid = seeded_tenants["non_hipaa"]
    slug = "enforce-agent"
    await _seed_trust(db_session, slug, tid, TrustTier.T2_TRUSTED)
    await db_session.commit()

    changes = await evaluate_and_apply_tier_changes(
        db=db_session,
        audit_db=db_session,
        updates=[_demoting_update(slug, tid)],
        drifts={(slug, tid, _VIS): _demoting_drift(slug, tid)},
        daemon_run_id=uuid.uuid4(),
        shadow=False,
    )

    # Enforce mode: the demotion is applied + an applied action_event emitted.
    assert len(changes) == 1
    assert changes[0].kind == "demote"
    assert changes[0].action_event_id is not None
    assert await _current_tier(db_session, slug, tid) == int(TrustTier.T1_TRAINEE)
    assert await _demote_event_count(db_session, tid) == 1
