from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from contact_ops.agents.dedup.merge_executor import apply_merge
from contact_ops.agents.dedup.unmerge_executor import unmerge

pytestmark = pytest.mark.asyncio


async def _seed_source(
    db_session,
    source_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> None:
    await db_session.execute(
        text("""
            INSERT INTO sources (id, source_type, tenant_id)
            VALUES (:id, 'manual_entry', :tid)
        """),
        {"id": str(source_id), "tid": str(tenant_id)},
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


def _facts_snapshot(db_session, person_id: uuid.UUID):
    """Return a set of (predicate, object_value) tuples for a person."""
    from sqlalchemy import text as _text
    rows = db_session.execute(
        _text("""
            SELECT predicate, object_value
            FROM facts
            WHERE subject_id = :sid AND subject_kind = 'person'
              AND superseded_by_fact_id IS NULL
        """),
        {"sid": str(person_id)},
    )
    return {(r.predicate, json.dumps(r.object_value, sort_keys=True)) for r in rows}


async def test_unmerge_round_trip(db_session, seeded_tenants):
    tenant_id = seeded_tenants["non_hipaa"]
    source_id = uuid.uuid4()
    await _seed_source(db_session, source_id, tenant_id)

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
            "name": "Alias Alice",
            "gn": "Alice",
            "fn": "Smith",
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
            "name": "Survivor Bob",
            "gn": "Bob",
            "fn": "Jones",
        },
    )

    # Facts on alias: email + phone
    alias_email_fact = uuid.uuid4()
    alias_phone_fact = uuid.uuid4()
    await _seed_fact(db_session, alias_email_fact, tenant_id, alias_id, "email", {"value": "REMOVED"}, source_id)
    await _seed_fact(db_session, alias_phone_fact, tenant_id, alias_id, "phone", {"value": "+12025551234"}, source_id)

    # Facts on survivor: email only (doesn't overlap with alias email)
    await _seed_fact(
        db_session, uuid.uuid4(), tenant_id, survivor_id, "email", {"value": "REMOVED"}, source_id,
    )

    # Capture original fact sets
    alias_facts_before = _facts_snapshot(db_session, alias_id)
    survivor_facts_before = _facts_snapshot(db_session, survivor_id)

    # Action event
    action_event_id = uuid.uuid4()
    merge_history_id = uuid.uuid4()
    evidence = {
        "person_a_id": str(alias_id),
        "person_b_id": str(survivor_id),
        "merge_history_id": str(merge_history_id),
    }

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

    # Apply merge
    merge_result = await apply_merge(
        action_event_id=action_event_id,
        db=db_session,
        audit_db=db_session,
    )
    assert merge_result.facts_promoted > 0

    # --- Unmerge ---
    unmerge_result = await unmerge(
        person_alias_id=alias_id,
        db=db_session,
        audit_db=db_session,
    )

    assert unmerge_result.alias_id == alias_id
    assert unmerge_result.survivor_id == survivor_id

    # Verify fact sets match original state
    alias_facts_after = _facts_snapshot(db_session, alias_id)
    survivor_facts_after = _facts_snapshot(db_session, survivor_id)

    assert alias_facts_after == alias_facts_before, (
        f"Alias facts differ after unmerge:\n  before={alias_facts_before}\n  after={alias_facts_after}"
    )
    assert survivor_facts_after == survivor_facts_before, (
        f"Survivor facts differ after unmerge:\n  before={survivor_facts_before}\n  after={survivor_facts_after}"
    )

    # Alias person restored to canonical
    alias_row = (await db_session.execute(
        text("SELECT merge_status, merged_into_id FROM persons WHERE id = :id"),
        {"id": str(alias_id)},
    )).mappings().one()
    assert alias_row["merge_status"] == "canonical"
    assert alias_row["merged_into_id"] is None

    # person_alias row deleted
    pa_count = (await db_session.execute(
        text("SELECT COUNT(*) AS cnt FROM person_alias WHERE alias_id = :aid"),
        {"aid": str(alias_id)},
    )).scalar_one()
    assert pa_count == 0

    # prov_activities row written with type='merge_unmerge'
    prov = await db_session.execute(
        text("""
            SELECT id FROM prov_activities
            WHERE activity_type = 'merge_unmerge'
              AND inputs @> ARRAY[:aid::uuid]
        """),
        {"aid": str(alias_id)},
    )
    assert prov.mappings().first() is not None, "expected prov_activities of type merge_unmerge"

    # Verify merge_history has reverted_at
    mh = await db_session.execute(
        text("SELECT reverted_at, reverted_event_id FROM merge_history WHERE id = :id"),
        {"id": str(merge_history_id)},
    )
    mh_row = mh.mappings().one()
    assert mh_row["reverted_at"] is not None
    assert mh_row["reverted_event_id"] is not None
