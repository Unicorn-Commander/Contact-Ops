"""Reversibility + collision coverage for the one-click person merge.

Locks in the invariants the adversarial review flagged as untested: a merge then
unmerge restores the pre-merge state EXACTLY, shared-value emails are de-duped
(not collided), a moved primary is demoted (one-primary-per-person held) and
restored on unmerge, and a cross-tenant merge is refused. Mirrors the harness in
test_mcp_tools_dedup_admin.py (superuser db_session, rolled back per test).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from contact_ops.mcp.errors import PERSON_NOT_FOUND, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.data_quality import (
    MergePeopleInput,
    UnmergePeopleInput,
    merge_people,
    unmerge_people,
)

pytestmark = pytest.mark.asyncio


def _ctx(db_session, *, tenant_id: uuid.UUID) -> MCPContext:
    return MCPContext(
        tenant_id=tenant_id,
        user_id=str(uuid.uuid4()),
        actor_chain={"sub": "test-staff", "act": {"sub": "system"}},
        human_authority=str(tenant_id),
        db=db_session,
        audit_db=db_session,
        request_id="test-request",
        claims={"realm_access": {"roles": ["STAFF"]}, "scope": "person:read person:write"},
    )


async def _seed_person(db_session, person_id: uuid.UUID, tenant_id: uuid.UUID, name: str) -> None:
    await db_session.execute(
        text(
            "INSERT INTO persons (id, canonical_owner_tenant_id, merge_status, display_name, "
            "created_at, updated_at) VALUES (:id, :tid, 'canonical', :name, now(), now())"
        ),
        {"id": str(person_id), "tid": str(tenant_id), "name": name},
    )


async def _seed_email(db_session, person_id: uuid.UUID, address: str, *, primary: bool) -> None:
    await db_session.execute(
        text("INSERT INTO emails (person_id, address, is_primary) VALUES (:p, :a, :pr)"),
        {"p": str(person_id), "a": address, "pr": primary},
    )


async def _scalar(db_session, sql: str, **params):
    return (await db_session.execute(text(sql), params)).scalar()


async def test_merge_people_roundtrip_restores_exactly(db_session, seeded_tenants):
    """merge then unmerge restores the loser, its primary flag, and the survivor."""
    tid = seeded_tenants["non_hipaa"]
    survivor, loser = uuid.uuid4(), uuid.uuid4()
    await _seed_person(db_session, survivor, tid, "Survivor One")
    await _seed_person(db_session, loser, tid, "Loser Two")
    # survivor keeps shared@x as its primary; loser carries the shared one (non-primary,
    # so it value-collides and is LEFT behind) plus a distinct primary that must move+demote.
    await _seed_email(db_session, survivor, "REMOVED", primary=True)
    await _seed_email(db_session, loser, "REMOVED", primary=False)
    await _seed_email(db_session, loser, "REMOVED", primary=True)
    await db_session.execute(
        text("INSERT INTO phones (person_id, e164, is_primary) VALUES (:p, :e, true)"),
        {"p": str(loser), "e": "+15550001111"},
    )
    await db_session.commit()
    ctx = _ctx(db_session, tenant_id=tid)

    dry = await merge_people(ctx, MergePeopleInput(survivor_id=survivor, loser_ids=[loser], dry_run=True))
    assert dry.status == "planned" and dry.merge_event_id is None and dry.emails_consolidated == 2
    assert await _scalar(db_session, "SELECT merge_status FROM persons WHERE id=:i", i=str(loser)) == "canonical"

    res = await merge_people(ctx, MergePeopleInput(survivor_id=survivor, loser_ids=[loser], dry_run=False))
    assert res.status == "applied" and res.merge_event_id
    assert await _scalar(db_session, "SELECT merged_into_id FROM persons WHERE id=:i", i=str(loser)) == survivor
    # shared value stayed on the loser (no unique-index violation), not duplicated onto survivor
    assert await _scalar(db_session, "SELECT count(*) FROM emails WHERE person_id=:p AND address='REMOVED'", p=str(loser)) == 1
    assert await _scalar(db_session, "SELECT count(*) FROM emails WHERE person_id=:p AND address='REMOVED'", p=str(survivor)) == 1
    # distinct primary moved to survivor and was demoted (survivor keeps a single primary)
    assert await _scalar(db_session, "SELECT is_primary FROM emails WHERE person_id=:p AND address='REMOVED'", p=str(survivor)) is False
    assert await _scalar(db_session, "SELECT count(*) FROM emails WHERE person_id=:p AND is_primary", p=str(survivor)) == 1
    assert await _scalar(db_session, "SELECT is_primary FROM phones WHERE person_id=:p", p=str(survivor)) is True

    un = await unmerge_people(ctx, UnmergePeopleInput(merge_event_id=uuid.UUID(res.merge_event_id)))
    assert un.status == "applied" and un.restored == [str(loser)]
    assert await _scalar(db_session, "SELECT merge_status FROM persons WHERE id=:i", i=str(loser)) == "canonical"
    assert await _scalar(db_session, "SELECT merged_into_id FROM persons WHERE id=:i", i=str(loser)) is None
    # moved primary back on the loser with is_primary restored; phone back; survivor down to its own email
    assert await _scalar(db_session, "SELECT is_primary FROM emails WHERE person_id=:p AND address='REMOVED'", p=str(loser)) is True
    assert await _scalar(db_session, "SELECT count(*) FROM phones WHERE person_id=:p", p=str(loser)) == 1
    assert await _scalar(db_session, "SELECT count(*) FROM emails WHERE person_id=:p", p=str(survivor)) == 1


async def test_merge_people_cross_tenant_rejected(db_session, seeded_tenants):
    """A caller scoped to another tenant cannot merge this tenant's people."""
    tid = seeded_tenants["non_hipaa"]
    other = seeded_tenants["hipaa"]
    survivor, loser = uuid.uuid4(), uuid.uuid4()
    await _seed_person(db_session, survivor, tid, "Survivor")
    await _seed_person(db_session, loser, tid, "Loser")
    await db_session.commit()

    ctx = _ctx(db_session, tenant_id=other)
    with pytest.raises(ToolError) as exc:
        await merge_people(ctx, MergePeopleInput(survivor_id=survivor, loser_ids=[loser], dry_run=False))
    assert exc.value.code == PERSON_NOT_FOUND
