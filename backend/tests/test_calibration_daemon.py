"""Phase 3.4 Calibration Daemon end-to-end tests.

Exercises the full pass: seed action_event rows -> run_calibration_pass
-> assert agent_trust updated, calibration_run_log written, tier_change
proposals emitted for the right (agent × tenant × visibility) tuples.

The Foundation-layer trust math (posteriors, drift, tier_from_posterior)
already has its own coverage in test_trust_ladder.py — this file tests
the *orchestration*: which rows get walked, when promotions vs demotions
fire, idempotency of the daily walk.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from contact_ops.agents.calibration import run_calibration_pass
from contact_ops.agents.trust import TrustTier


@pytest_asyncio.fixture
async def _tenant(db_session):
    tid = "00000000-0000-0000-0000-000000000c41"
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix)
            VALUES (CAST(:id AS uuid), 'calibration-tenant', 'brand',
                    'Calibration Tenant', CAST(:id AS uuid), false,
                    'cal-ns', 'cal-bkt')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": tid},
    )
    await db_session.commit()
    return uuid.UUID(tid)


async def _seed_agent_outcome(
    db_session,
    *,
    tenant_id,
    agent_slug,
    status,
    visibility="private",
    days_ago: float = 0.0,
):
    """Helper: insert one action_event in the past with the given outcome."""
    proposed_at = datetime.now(UTC) - timedelta(days=days_ago)
    applied_at = proposed_at if status in ("applied", "approved") else None
    await db_session.execute(
        text(
            """
            INSERT INTO action_event (
                event_type, tenant_id, aggregate_type, aggregate_id,
                payload, actor, actor_type, evidence, status,
                content_hash, proposed_at, applied_at,
                idempotency_key, decision_payload, reversibility_class,
                trust_tier_at_creation
            ) VALUES (
                'test.event', CAST(:tenant AS uuid), 'person'::entity_kind,
                gen_random_uuid(),
                '{}'::jsonb,
                CAST(:actor AS jsonb), 'agent'::actor_type,
                '{}'::jsonb, CAST(:status AS event_status),
                gen_random_bytes(32), :proposed_at, :applied_at,
                gen_random_uuid()::text,
                CAST(:decision_payload AS jsonb), 'reversible',
                2
            )
            """
        ),
        {
            "tenant": str(tenant_id),
            "actor": f'{{"sub": "{agent_slug}"}}',
            "status": status,
            "proposed_at": proposed_at,
            "applied_at": applied_at,
            "decision_payload": f'{{"visibility": "{visibility}"}}',
        },
    )


@pytest.mark.asyncio
async def test_first_pass_creates_agent_trust_rows(db_session, _tenant):
    """Day-1 calibration: posteriors UPSERT creates rows for each agent seen."""
    # Seed 10 approvals + 2 rejections for "dedup" agent
    for _ in range(10):
        await _seed_agent_outcome(
            db_session, tenant_id=_tenant, agent_slug="dedup", status="applied"
        )
    for _ in range(2):
        await _seed_agent_outcome(
            db_session, tenant_id=_tenant, agent_slug="dedup", status="reverted"
        )
    await db_session.commit()

    result = await run_calibration_pass(db=db_session, audit_db=db_session)

    assert result.posteriors_updated == 1
    row = (
        await db_session.execute(
            text(
                """
                SELECT alpha, beta, samples_total, samples_30d, current_tier
                FROM agent_trust
                WHERE agent_slug = 'dedup'
                  AND tenant_id = CAST(:t AS uuid)
                """
            ),
            {"t": str(_tenant)},
        )
    ).mappings().one()
    # Beta(1+10, 1+2) = Beta(11, 3); mean ≈ 0.79.
    assert float(row["alpha"]) == pytest.approx(11.0)
    assert float(row["beta"]) == pytest.approx(3.0)
    assert int(row["samples_total"]) == 12
    assert int(row["samples_30d"]) == 12


@pytest.mark.asyncio
async def test_calibration_run_log_records_pass(db_session, _tenant):
    """Every pass writes one calibration_run_log row with stats."""
    await _seed_agent_outcome(
        db_session, tenant_id=_tenant, agent_slug="tag", status="applied"
    )
    await db_session.commit()
    result = await run_calibration_pass(db=db_session, audit_db=db_session)
    row = (
        await db_session.execute(
            text(
                "SELECT posteriors_updated, drifts_evaluated, "
                "tier_promotes_proposed, tier_demotes_applied "
                "FROM calibration_run_log WHERE id = CAST(:id AS uuid)"
            ),
            {"id": str(result.daemon_run_id)},
        )
    ).mappings().one()
    assert int(row["posteriors_updated"]) >= 1
    assert int(row["drifts_evaluated"]) >= 1


@pytest.mark.asyncio
async def test_second_pass_only_walks_new_events(db_session, _tenant):
    """Idempotency: a second pass sees no new events and updates nothing."""
    await _seed_agent_outcome(
        db_session, tenant_id=_tenant, agent_slug="lifecycle", status="applied"
    )
    await db_session.commit()
    first = await run_calibration_pass(db=db_session, audit_db=db_session)
    assert first.posteriors_updated == 1

    # Second pass: no new events. last_run_at gates the walk.
    second = await run_calibration_pass(db=db_session, audit_db=db_session)
    assert second.posteriors_updated == 0


@pytest.mark.asyncio
async def test_demotion_triggers_on_15pp_drop(db_session, _tenant):
    """7d approval rate drops > 15pp below 30d -> auto-demotion."""
    # Bootstrap an existing agent_trust row at T2.
    await db_session.execute(
        text(
            """
            INSERT INTO agent_trust (
                agent_slug, tenant_id, visibility,
                alpha, beta, current_tier,
                samples_total, samples_7d, samples_30d,
                approval_rate_7d, approval_rate_30d, drift_status
            ) VALUES (
                'enrichment', CAST(:t AS uuid), 'private',
                95.0, 5.0, :tier,
                100, 0, 100,
                0.0, 0.95, 'stable'
            )
            """
        ),
        {"t": str(_tenant), "tier": int(TrustTier.T2_TRUSTED)},
    )
    # Seed: 60 approvals 15-25 days ago (30d baseline ≈ high), then
    # 25 rejections in the last 5 days (7d ≈ 0%). 30d - 7d gap > 15pp.
    for _ in range(60):
        await _seed_agent_outcome(
            db_session,
            tenant_id=_tenant,
            agent_slug="enrichment",
            status="applied",
            days_ago=20,
        )
    for _ in range(25):
        await _seed_agent_outcome(
            db_session,
            tenant_id=_tenant,
            agent_slug="enrichment",
            status="reverted",
            days_ago=2,
        )
    await db_session.commit()

    result = await run_calibration_pass(db=db_session, audit_db=db_session)
    assert result.tier_demotes_applied >= 1

    new_tier = (
        await db_session.execute(
            text(
                "SELECT current_tier FROM agent_trust "
                "WHERE agent_slug = 'enrichment' AND tenant_id = CAST(:t AS uuid)"
            ),
            {"t": str(_tenant)},
        )
    ).scalar_one()
    assert int(new_tier) == int(TrustTier.T1_TRAINEE)


@pytest.mark.asyncio
async def test_promotion_emits_proposal_not_auto_apply(db_session, _tenant):
    """Promotion writes status=proposed action_event for Aaron's inbox review."""
    # Existing T0 row with a posterior that now implies T1+.
    await db_session.execute(
        text(
            """
            INSERT INTO agent_trust (
                agent_slug, tenant_id, visibility,
                alpha, beta, current_tier,
                samples_total, samples_7d, samples_30d,
                drift_status, warning_streak
            ) VALUES (
                'tag', CAST(:t AS uuid), 'private',
                26.0, 1.0, :tier,
                25, 25, 25,
                'stable', 0
            )
            """
        ),
        {"t": str(_tenant), "tier": int(TrustTier.T0_PROBATION)},
    )
    # Seed 50 more approvals → posterior_tier becomes T1 (mean 0.95+ at n=75).
    for _ in range(50):
        await _seed_agent_outcome(
            db_session,
            tenant_id=_tenant,
            agent_slug="tag",
            status="applied",
            days_ago=1,
        )
    await db_session.commit()

    result = await run_calibration_pass(db=db_session, audit_db=db_session)
    # Promotion proposal but NOT auto-applied.
    assert result.tier_promotes_proposed >= 1

    proposal = (
        await db_session.execute(
            text(
                """
                SELECT status, event_type, decision_payload
                FROM action_event
                WHERE event_type = 'calibration.tier_promote'
                  AND tenant_id = CAST(:t AS uuid)
                """
            ),
            {"t": str(_tenant)},
        )
    ).mappings().first()
    assert proposal is not None
    assert proposal["status"] == "proposed"
    payload = proposal["decision_payload"]
    assert payload["payload_after"]["agent_slug"] == "tag"

    # current_tier on agent_trust is unchanged (still T0)
    current_tier = (
        await db_session.execute(
            text(
                "SELECT current_tier FROM agent_trust "
                "WHERE agent_slug = 'tag' AND tenant_id = CAST(:t AS uuid)"
            ),
            {"t": str(_tenant)},
        )
    ).scalar_one()
    assert int(current_tier) == int(TrustTier.T0_PROBATION)


@pytest.mark.asyncio
async def test_fleet_revert_rate_reported(db_session, _tenant):
    """fleet_revert_rate_pct reflects 24h applied vs reverted on action_event."""
    for _ in range(10):
        await _seed_agent_outcome(
            db_session, tenant_id=_tenant, agent_slug="lifecycle", status="applied"
        )
    for _ in range(2):
        await _seed_agent_outcome(
            db_session, tenant_id=_tenant, agent_slug="lifecycle", status="reverted"
        )
    await db_session.commit()
    result = await run_calibration_pass(db=db_session, audit_db=db_session)
    # 2 reverted / 10 applied = 20%
    assert result.fleet_revert_rate_pct == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_promote_then_apply_via_helper(db_session, _tenant):
    """apply_promotion bumps current_tier on the target agent_trust row."""
    from contact_ops.agents.calibration import apply_promotion

    await db_session.execute(
        text(
            """
            INSERT INTO agent_trust (
                agent_slug, tenant_id, visibility,
                alpha, beta, current_tier,
                samples_total, drift_status
            ) VALUES (
                'voice-match', CAST(:t AS uuid), 'private',
                1.0, 1.0, :tier,
                0, 'stable'
            )
            """
        ),
        {"t": str(_tenant), "tier": int(TrustTier.T1_TRAINEE)},
    )
    # Manually write a calibration.tier_promote proposal we can apply.
    decision_payload = json.dumps({
        "payload_after": {
            "agent_slug": "voice-match",
            "tenant_id": str(_tenant),
            "visibility": "private",
            "to_tier": int(TrustTier.T2_TRUSTED),
        }
    })
    result = await db_session.execute(
        text(
            """
            INSERT INTO action_event (
                event_type, tenant_id, aggregate_type, aggregate_id,
                payload, actor, actor_type, evidence, status,
                content_hash, decision_payload, reversibility_class,
                idempotency_key
            ) VALUES (
                'calibration.tier_promote', CAST(:t AS uuid),
                'person'::entity_kind, gen_random_uuid(),
                '{}'::jsonb,
                CAST(:actor AS jsonb), 'agent'::actor_type,
                '{}'::jsonb, 'proposed'::event_status,
                gen_random_bytes(32),
                CAST(:decision AS jsonb), 'reversible',
                gen_random_uuid()::text
            )
            RETURNING event_id
            """
        ),
        {
            "t": str(_tenant),
            "actor": '{"sub": "calibration-daemon"}',
            "decision": decision_payload,
        },
    )
    event_id = uuid.UUID(str(result.scalar_one()))
    await db_session.commit()

    await apply_promotion(
        db=db_session, audit_db=db_session, proposal_event_id=event_id
    )
    await db_session.commit()

    new_tier = (
        await db_session.execute(
            text(
                "SELECT current_tier FROM agent_trust "
                "WHERE agent_slug = 'voice-match' AND tenant_id = CAST(:t AS uuid)"
            ),
            {"t": str(_tenant)},
        )
    ).scalar_one()
    assert int(new_tier) == int(TrustTier.T2_TRUSTED)
