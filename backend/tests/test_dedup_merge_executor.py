from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from contact_ops.agents.dedup.merge_executor import MergeResult, apply_merge

pytestmark = pytest.mark.asyncio


async def _seed_source(
    db_session,
    source_id: uuid.UUID,
    tenant_id: uuid.UUID,
    source_type: str = "manual_entry",
) -> None:
    await db_session.execute(
        text("""
            INSERT INTO sources (id, source_type, tenant_id)
            VALUES (:id, :st, :tid)
        """),
        {"id": str(source_id), "st": source_type, "tid": str(tenant_id)},
    )


async def _seed_fact(
    db_session,
    fact_id: uuid.UUID,
    tenant_id: uuid.UUID,
    subject_id: uuid.UUID,
    predicate: str,
    object_value: dict,
    source_id: uuid.UUID,
) -> None:
    await db_session.execute(
        text("""
            INSERT INTO facts (id, tenant_id, subject_kind, subject_id,
                               predicate, object_kind, object_value, source_id,
                               confidence, observed_at)
            VALUES (:id, :tid, 'person', :sid,
                    :pred, 'text', CAST(:ov AS jsonb), :src,
                    1.0, now())
        """),
        {
            "id": str(fact_id),
            "tid": str(tenant_id),
            "sid": str(subject_id),
            "pred": predicate,
            "ov": json.dumps(object_value),
            "src": str(source_id),
        },
    )


def _evidence(
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
    merge_history_id: uuid.UUID,
) -> dict:
    return {
        "person_a_id": str(person_a_id),
        "person_b_id": str(person_b_id),
        "merge_history_id": str(merge_history_id),
    }


async def test_apply_merge_happy_path(db_session, seeded_tenants):
    tenant_id = seeded_tenants["non_hipaa"]
    source_id = uuid.uuid4()
    await _seed_source(db_session, source_id, tenant_id)

    # Two persons
    alias_id = uuid.uuid4()
    survivor_id = uuid.uuid4()

    await db_session.execute(
        text("""
            INSERT INTO persons (id, canonical_owner_tenant_id, merge_status,
                                 display_name, given_name, family_name,
                                 created_at, updated_at)
            VALUES (:id, :tid, 'canonical', :name, :gn, :fn, now(), now())
        """),
        {
            "id": str(alias_id),
            "tid": str(tenant_id),
            "name": "Alias Person",
            "gn": "Alias",
            "fn": "Person",
        },
    )
    await db_session.execute(
        text("""
            INSERT INTO persons (id, canonical_owner_tenant_id, merge_status,
                                 display_name, given_name, family_name,
                                 created_at, updated_at)
            VALUES (:id, :tid, 'canonical', :name, :gn, :fn, now(), now())
        """),
        {
            "id": str(survivor_id),
            "tid": str(tenant_id),
            "name": "Survivor Person",
            "gn": "Survivor",
            "fn": "Person",
        },
    )

    # Facts on alias (some overlapping, some unique)
    fact_overlap_id = uuid.uuid4()
    fact_unique_id = uuid.uuid4()
    await _seed_fact(db_session, fact_overlap_id, tenant_id, alias_id, "email", {"value": "REMOVED"}, source_id)
    await _seed_fact(db_session, fact_unique_id, tenant_id, alias_id, "phone", {"value": "+12025551234"}, source_id)

    # Facts on survivor (share the email, so only phone should promote)
    await _seed_fact(
        db_session, uuid.uuid4(), tenant_id, survivor_id, "email", {"value": "REMOVED"}, source_id,
    )

    # Action event (propose_merge)
    action_event_id = uuid.uuid4()
    merge_history_id = uuid.uuid4()
    evidence = _evidence(alias_id, survivor_id, merge_history_id)

    await db_session.execute(
        text("""
            INSERT INTO action_event (event_id, event_type, tenant_id,
                aggregate_type, aggregate_id, payload, actor, actor_type,
                evidence, status, content_hash)
            VALUES (:eid, 'dedup.propose_merge', :tid,
                'person', :aid, '{}'::jsonb, '{}'::jsonb, 'agent',
                CAST(:ev AS jsonb), 'proposed',
                '\\x0000000000000000000000000000000000000000000000000000000000000000'::bytea)
        """),
        {
            "eid": str(action_event_id),
            "tid": str(tenant_id),
            "aid": str(alias_id),
            "ev": json.dumps(evidence),
        },
    )

    # Merge history
    await db_session.execute(
        text("""
            INSERT INTO merge_history (id, tenant_id, entity_type, kept_id, removed_id,
                                       score, performed_event_id, performed_at)
            VALUES (:id, :tid, 'person', :kept, :removed, 0.95,
                    :evid, now())
        """),
        {
            "id": str(merge_history_id),
            "tid": str(tenant_id),
            "kept": str(survivor_id),
            "removed": str(alias_id),
            "evid": str(action_event_id),
        },
    )

    await db_session.commit()

    result: MergeResult = await apply_merge(
        action_event_id=action_event_id,
        db=db_session,
        audit_db=db_session,
    )

    assert result.survivor_id == survivor_id
    assert result.alias_id == alias_id
    assert result.facts_promoted == 1  # only the phone fact should promote
    assert result.can_unmerge is True

    # Verify merge_history row was updated
    mh = await db_session.execute(
        text("SELECT merge_activity_id, can_unmerge, reversible_until FROM merge_history WHERE id = :id"),
        {"id": str(merge_history_id)},
    )
    mh_row = mh.mappings().one()
    assert mh_row["merge_activity_id"] is not None
    assert mh_row["can_unmerge"] is True

    # Verify person_alias row exists
    pa = await db_session.execute(
        text("SELECT alias_id, current_canonical_id FROM person_alias WHERE alias_id = :aid"),
        {"aid": str(alias_id)},
    )
    pa_row = pa.mappings().one()
    assert str(pa_row["current_canonical_id"]) == str(survivor_id)

    # Verify alias person has merge_status='merged_into' and merged_into_id set
    alias_check = await db_session.execute(
        text("SELECT merge_status, merged_into_id FROM persons WHERE id = :id"),
        {"id": str(alias_id)},
    )
    alias_row = alias_check.mappings().one()
    assert alias_row["merge_status"] == "merged_into"
    assert str(alias_row["merged_into_id"]) == str(survivor_id)

    # Verify the phone fact was promoted to survivor
    prom = await db_session.execute(
        text("""
            SELECT id FROM facts
            WHERE subject_id = :sid AND predicate = 'phone'
              AND was_revision_of IS NOT NULL
        """),
        {"sid": str(survivor_id)},
    )
    assert prom.mappings().first() is not None, "phone fact should be promoted"


async def test_apply_merge_idempotent(db_session, seeded_tenants):
    tenant_id = seeded_tenants["non_hipaa"]
    source_id = uuid.uuid4()
    await _seed_source(db_session, source_id, tenant_id)

    alias_id = uuid.uuid4()
    survivor_id = uuid.uuid4()

    await db_session.execute(
        text("""
            INSERT INTO persons (id, canonical_owner_tenant_id, merge_status,
                                 display_name, created_at, updated_at)
            VALUES (:id, :tid, 'canonical', :name, now(), now())
        """),
        {"id": str(alias_id), "tid": str(tenant_id), "name": "Alias"},
    )
    await db_session.execute(
        text("""
            INSERT INTO persons (id, canonical_owner_tenant_id, merge_status,
                                 display_name, created_at, updated_at)
            VALUES (:id, :tid, 'canonical', :name, now(), now())
        """),
        {"id": str(survivor_id), "tid": str(tenant_id), "name": "Survivor"},
    )

    fact_id = uuid.uuid4()
    await _seed_fact(db_session, fact_id, tenant_id, alias_id, "email", {"value": "REMOVED"}, source_id)

    action_event_id = uuid.uuid4()
    merge_history_id = uuid.uuid4()
    evidence = _evidence(alias_id, survivor_id, merge_history_id)

    await db_session.execute(
        text("""
            INSERT INTO action_event (event_id, event_type, tenant_id,
                aggregate_type, aggregate_id, payload, actor, actor_type,
                evidence, status, content_hash)
            VALUES (:eid, 'dedup.propose_merge', :tid,
                'person', :aid, '{}'::jsonb, '{}'::jsonb, 'agent',
                CAST(:ev AS jsonb), 'proposed',
                '\\x0000000000000000000000000000000000000000000000000000000000000000'::bytea)
        """),
        {
            "eid": str(action_event_id),
            "tid": str(tenant_id),
            "aid": str(alias_id),
            "ev": json.dumps(evidence),
        },
    )

    await db_session.execute(
        text("""
            INSERT INTO merge_history (id, tenant_id, entity_type, kept_id, removed_id,
                                       score, performed_event_id, performed_at)
            VALUES (:id, :tid, 'person', :kept, :removed, 0.95,
                    :evid, now())
        """),
        {
            "id": str(merge_history_id),
            "tid": str(tenant_id),
            "kept": str(survivor_id),
            "removed": str(alias_id),
            "evid": str(action_event_id),
        },
    )

    await db_session.commit()

    # First call succeeds
    result1 = await apply_merge(
        action_event_id=action_event_id,
        db=db_session,
        audit_db=db_session,
    )
    assert result1.facts_promoted >= 0

    # Second call with same action_event_id must fail (person_alias PK violation)
    with pytest.raises(Exception):
        await apply_merge(
            action_event_id=action_event_id,
            db=db_session,
            audit_db=db_session,
        )

    # Verify no duplicate side effects — only one person_alias row
    pa_count = await db_session.execute(
        text("SELECT COUNT(*) AS cnt FROM person_alias WHERE alias_id = :aid"),
        {"aid": str(alias_id)},
    )
    assert pa_count.scalar_one() == 1

    # Only one promoted fact (was_revision_of points to the original)
    prom_count = await db_session.execute(
        text("""
            SELECT COUNT(*) AS cnt FROM facts
            WHERE was_revision_of = :fid
        """),
        {"fid": str(fact_id)},
    )
    assert prom_count.scalar_one() == 1
