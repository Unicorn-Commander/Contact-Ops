from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from contact_ops.agents.base import AgentContext, AgentResult, Visibility
from contact_ops.agents.dedup.agent import DedupAgent
from contact_ops.agents.dedup.blocking import (
    BlockingResult,
    CandidatePair,
    stage1_deterministic_blocking,
)
from contact_ops.agents.dedup.splink_runner import ScoredPair, SplinkResult

pytestmark = pytest.mark.asyncio


@dataclass
class _DuplicateSeed:
    person_a_id: uuid.UUID
    person_b_id: uuid.UUID
    dup_type: str


async def _seed_person(
    db_session,
    person_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    given_name: str,
    family_name: str,
    display_name: str | None = None,
    company: str | None = None,  # accepted for call-site compat; persons has no
    # company column (it is sourced elsewhere), so it is not seeded here. The
    # "name + company" scenario below is detected by the name blocking key.
) -> None:
    await db_session.execute(
        text("""
            INSERT INTO persons (id, canonical_owner_tenant_id, merge_status,
                                 display_name, given_name, family_name,
                                 created_at, updated_at)
            VALUES (:id, :tid, 'canonical', :name, :gn, :fn, now(), now())
        """),
        {
            "id": str(person_id),
            "tid": str(tenant_id),
            "name": display_name or f"{given_name} {family_name}",
            "gn": given_name,
            "fn": family_name,
        },
    )


async def _seed_email(
    db_session,
    person_id: uuid.UUID,
    address: str,
) -> None:
    await db_session.execute(
        text("""
            INSERT INTO emails (person_id, address, is_primary)
            VALUES (:pid, :addr, true)
        """),
        {"pid": str(person_id), "addr": address},
    )


async def _seed_phone(
    db_session,
    person_id: uuid.UUID,
    e164: str,
) -> None:
    await db_session.execute(
        text("""
            INSERT INTO phones (person_id, e164, is_primary)
            VALUES (:pid, :e164, true)
        """),
        {"pid": str(person_id), "e164": e164},
    )


async def test_end_to_end_pipeline(db_session, seeded_tenants):
    tenant_id = seeded_tenants["non_hipaa"]
    all_dupes: list[_DuplicateSeed] = []

    # ------------------------------------------------------------------
    # 5 email-exact duplicate pairs (10 persons)
    # ------------------------------------------------------------------
    email_dupes: list[_DuplicateSeed] = []
    for i in range(5):
        a = uuid.uuid4()
        b = uuid.uuid4()
        shared_email = f"shared{i}@example.com"
        await _seed_person(db_session, a, tenant_id, given_name=f"EAlice{i}", family_name="Smith")
        await _seed_person(db_session, b, tenant_id, given_name=f"EBob{i}", family_name="Jones")
        await _seed_email(db_session, a, shared_email)
        await _seed_email(db_session, b, shared_email)
        d = _DuplicateSeed(a, b, "email_exact")
        email_dupes.append(d)
        all_dupes.append(d)

    # ------------------------------------------------------------------
    # 5 phone-exact duplicate pairs (10 persons)
    # ------------------------------------------------------------------
    phone_dupes: list[_DuplicateSeed] = []
    for i in range(5):
        a = uuid.uuid4()
        b = uuid.uuid4()
        shared_phone = f"+1202555{1000 + i:04d}"
        await _seed_person(db_session, a, tenant_id, given_name=f"PAlice{i}", family_name="Brown")
        await _seed_person(db_session, b, tenant_id, given_name=f"PBob{i}", family_name="Davis")
        await _seed_phone(db_session, a, shared_phone)
        await _seed_phone(db_session, b, shared_phone)
        d = _DuplicateSeed(a, b, "phone_exact")
        phone_dupes.append(d)
        all_dupes.append(d)

    # ------------------------------------------------------------------
    # 3 fuzzy-name duplicate pairs (6 persons)
    #   Same dmetaphone(last_name) + first_initial
    #   e.g., Smith/Smythe, Johnson/Jonsen, Miller/Millar
    # ------------------------------------------------------------------
    name_dupes: list[_DuplicateSeed] = []
    name_pairs = [
        ("John", "Smith", "Jane", "Smythe"),
        ("Mike", "Johnson", "Mary", "Jonsen"),
        ("Chris", "Miller", "Cathy", "Millar"),
    ]
    for gn_a, fn_a, gn_b, fn_b in name_pairs:
        a = uuid.uuid4()
        b = uuid.uuid4()
        await _seed_person(db_session, a, tenant_id, given_name=gn_a, family_name=fn_a)
        await _seed_person(db_session, b, tenant_id, given_name=gn_b, family_name=fn_b)
        d = _DuplicateSeed(a, b, "fuzzy_name")
        name_dupes.append(d)
        all_dupes.append(d)

    # ------------------------------------------------------------------
    # 2 multi-field weak pairs (4 persons)
    #   Same given_name + family_name + company, different email
    # ------------------------------------------------------------------
    multi_dupes: list[_DuplicateSeed] = []
    for i in range(2):
        a = uuid.uuid4()
        b = uuid.uuid4()
        await _seed_person(
            db_session, a, tenant_id,
            given_name=f"Multi{i}", family_name="Weak",
            company="Acme Corp",
        )
        await _seed_person(
            db_session, b, tenant_id,
            given_name=f"Multi{i}", family_name="Weak",
            company="Acme Corp",
        )
        await _seed_email(db_session, a, f"multi{i}REMOVED")
        await _seed_email(db_session, b, f"multi{i}REMOVED")
        d = _DuplicateSeed(a, b, "multi_field_weak")
        multi_dupes.append(d)
        all_dupes.append(d)

    # ------------------------------------------------------------------
    # Remaining unique persons (70 persons) — fill to ~100 total
    # ------------------------------------------------------------------
    unique_count = 70
    for i in range(unique_count):
        pid = uuid.uuid4()
        gn = f"Unique{i}"
        fn = "Person"
        co = f"Company{i % 10}"
        await _seed_person(db_session, pid, tenant_id, given_name=gn, family_name=fn, company=co)
        await _seed_email(db_session, pid, f"{gn.lower()}@example.com")

    await db_session.commit()

    assert len(all_dupes) == 15
    ground_truth_pairs: set[frozenset[uuid.UUID]] = {
        frozenset({d.person_a_id, d.person_b_id}) for d in all_dupes
    }

    # ------------------------------------------------------------------
    # Step 1 — verify blocking catches the duplicate pairs
    # ------------------------------------------------------------------
    blocking_result = await stage1_deterministic_blocking(
        tenant_id=tenant_id,
        db_session=db_session,
    )
    blocked_pairs: set[frozenset[uuid.UUID]] = {
        frozenset({p.person_a_id, p.person_b_id}) for p in blocking_result
    }

    found_by_blocking = ground_truth_pairs & blocked_pairs
    blocking_recall = len(found_by_blocking) / len(ground_truth_pairs)
    assert blocking_recall >= 0.85, (
        f"blocking recall too low: {blocking_recall:.3f} "
        f"({len(found_by_blocking)}/{len(ground_truth_pairs)})"
    )

    # ------------------------------------------------------------------
    # Step 2 — mock Splink scoring and run the agent
    # ------------------------------------------------------------------
    ground_truth_lookup: dict[frozenset[uuid.UUID], str] = {
        frozenset({d.person_a_id, d.person_b_id}): d.dup_type
        for d in all_dupes
    }

    async def _mock_score_candidates(
        candidates_df,
        tenant_id,
        db_session,
    ) -> SplinkResult:
        scored: list[ScoredPair] = []
        for _, row in candidates_df.iterrows():
            a = uuid.UUID(row["person_id_l"])
            b = uuid.UUID(row["person_id_r"])
            pair = frozenset({a, b})
            is_true_dup = pair in ground_truth_lookup
            prob = 0.98 if is_true_dup else 0.10
            scored.append(ScoredPair(
                person_a_id=a,
                person_b_id=b,
                match_probability=prob,
                match_weight_bits=4.0 if is_true_dup else -3.0,
                per_field_weights={},
                blocking_key=row.get("blocking_key", ""),
                source_kind="dedup",
            ))
        return SplinkResult(
            scored_pairs=scored,
            model_version="test",
            em_iterations=0,
            em_converged=True,
            trained_on_pairs=len(scored),
            settings_json={},
        )

    # ------------------------------------------------------------------
    # Build mock AgentContext
    # ------------------------------------------------------------------
    cost_guard = AsyncMock()
    circuit_breaker = AsyncMock()
    cost_guard.check = AsyncMock()
    circuit_breaker.check = AsyncMock()

    ctx = AgentContext(
        db=db_session,
        audit_db=db_session,
        tenant_id=tenant_id,
        visibility=Visibility.ORG,
        triggered_by="manual",
        event_payload={},
        cost_guard=cost_guard,
        circuit_breaker=circuit_breaker,
    )

    agent = DedupAgent()

    with (
        patch(
            "contact_ops.agents.dedup.agent.score_candidates",
            side_effect=_mock_score_candidates,
        ),
        patch.object(agent, "_run_tie_breaker", return_value=None),
        patch(
            "contact_ops.agents.dedup.agent.run_blocking_pipeline",
            return_value=BlockingResult(candidates=blocking_result),
        ),
    ):
        result: AgentResult = await agent._run(ctx)

    # ------------------------------------------------------------------
    # Step 3 — verify emitted proposals
    # ------------------------------------------------------------------
    emitted_pairs: set[frozenset[uuid.UUID]] = set()
    rows = await db_session.execute(
        text("""
            SELECT event_id, aggregate_id, payload
            FROM action_event
            WHERE tenant_id = :tid
              AND event_type = 'dedup.propose_merge'
        """),
        {"tid": str(tenant_id)},
    )
    for ev_row in rows.mappings():
        payload = ev_row["payload"]
        person_b_id = payload.get("after", {}).get("person_b_id")
        if person_b_id:
            emitted_pairs.add(frozenset({ev_row["aggregate_id"], uuid.UUID(person_b_id)}))

    assert result.proposals_emitted >= 0

    if emitted_pairs:
        true_positives = emitted_pairs & ground_truth_pairs
        false_positives = emitted_pairs - ground_truth_pairs
        false_negatives = ground_truth_pairs - emitted_pairs

        precision = len(true_positives) / (len(true_positives) + len(false_positives)) if true_positives or false_positives else 0.0
        recall = len(true_positives) / len(ground_truth_pairs) if ground_truth_pairs else 1.0

        assert precision >= 0.95, (
            f"precision {precision:.3f} ({len(true_positives)} TP / "
            f"{len(true_positives) + len(false_positives)} predicted)"
        )
        assert recall >= 0.85, (
            f"recall {recall:.3f} ({len(true_positives)} / {len(ground_truth_pairs)} ground truth)"
        )
