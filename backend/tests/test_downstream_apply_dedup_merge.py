"""Approving / auto-applying a dedup proposal actually merges the two people.

Closes the gap where approval only flipped ``action_event.status='applied'`` and
never merged anyone, so duplicates persisted and the calibration loop learned a
positive outcome for a merge that never happened. These tests pin the executor's
contract:

* a single pairwise edge merges (loser tombstoned into survivor);
* re-applying the same edge is a no-op (idempotent), not a double-merge or error;
* the overlapping pairwise edges of a 3-person cluster converge on ONE survivor
  in any order, with chain resolution following ``merged_into_id`` (so the second
  edge whose endpoint is already merged does not error);
* a non-dedup event type is ignored (returns None, no effect).

Mirrors test_data_quality_merge_people.py: superuser db_session used for both the
app and audit session, rolled back per test.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from contact_ops.services.downstream_apply import (
    _canonical_root,
    apply_downstream_effect,
)

pytestmark = pytest.mark.asyncio

_ACTOR = {"sub": "test-reviewer"}


async def _seed_person(db, pid: uuid.UUID, tid: uuid.UUID, name: str) -> None:
    await db.execute(
        text(
            "INSERT INTO persons (id, canonical_owner_tenant_id, merge_status, "
            "display_name, created_at, updated_at) "
            "VALUES (:id, :tid, 'canonical', :name, now(), now())"
        ),
        {"id": str(pid), "tid": str(tid), "name": name},
    )


async def _merge_row(db, pid: uuid.UUID):
    return (
        await db.execute(
            text(
                "SELECT merge_status::text, merged_into_id "
                "FROM persons WHERE id = :i"
            ),
            {"i": str(pid)},
        )
    ).first()


async def _apply_edge(db, tid: uuid.UUID, survivor: uuid.UUID, alias: uuid.UUID):
    return await apply_downstream_effect(
        event_type="dedup.propose_merge",
        app_db=db,
        audit_db=db,
        tenant_id=tid,
        payload={
            "what_changes_if_merged": {
                "survivor_id": str(survivor),
                "alias_id": str(alias),
            }
        },
        confidence=0.99,
        actor_chain=_ACTOR,
        human_authority=str(tid),
        request_id="test",
    )


async def test_single_edge_merges(db_session, seeded_tenants):
    tid = seeded_tenants["non_hipaa"]
    a, b = uuid.uuid4(), uuid.uuid4()
    await _seed_person(db_session, a, tid, "A")
    await _seed_person(db_session, b, tid, "B")
    await db_session.commit()

    res = await _apply_edge(db_session, tid, a, b)

    assert res["status"] == "merged"
    assert res["survivor_id"] == str(a)
    assert (await _merge_row(db_session, a))[0] == "canonical"
    st_b = await _merge_row(db_session, b)
    assert st_b[0] == "merged_into"
    assert st_b[1] == a


async def test_reapplying_same_edge_is_noop(db_session, seeded_tenants):
    tid = seeded_tenants["non_hipaa"]
    a, b = uuid.uuid4(), uuid.uuid4()
    await _seed_person(db_session, a, tid, "A")
    await _seed_person(db_session, b, tid, "B")
    await db_session.commit()

    first = await _apply_edge(db_session, tid, a, b)
    second = await _apply_edge(db_session, tid, a, b)

    assert first["status"] == "merged"
    assert second["status"] == "already_merged"
    # still exactly one merge: b -> a, nothing double-applied
    assert (await _merge_row(db_session, b))[1] == a


async def test_cluster_converges_on_one_survivor(db_session, seeded_tenants):
    """3 dups, 3 overlapping pairwise edges, applied in chain-forcing order."""
    tid = seeded_tenants["non_hipaa"]
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for pid, name in ((a, "A"), (b, "B"), (c, "C")):
        await _seed_person(db_session, pid, tid, name)
    await db_session.commit()

    r1 = await _apply_edge(db_session, tid, a, b)  # b -> a
    # survivor 'b' is already merged; must root-resolve to 'a' and merge c into a
    r2 = await _apply_edge(db_session, tid, b, c)
    r3 = await _apply_edge(db_session, tid, a, c)  # both already root 'a'

    assert r1["status"] == "merged"
    assert r2["status"] == "merged"
    assert r3["status"] == "already_merged"

    canonical = [
        pid for pid in (a, b, c) if (await _merge_row(db_session, pid))[0] == "canonical"
    ]
    assert canonical == [a]
    assert await _canonical_root(db_session, b) == a
    assert await _canonical_root(db_session, c) == a


async def test_non_dedup_event_is_ignored(db_session, seeded_tenants):
    tid = seeded_tenants["non_hipaa"]
    res = await apply_downstream_effect(
        event_type="person.create",
        app_db=db_session,
        audit_db=db_session,
        tenant_id=tid,
        payload={"anything": True},
        confidence=1.0,
        actor_chain=_ACTOR,
        human_authority=str(tid),
        request_id="test",
    )
    assert res is None
