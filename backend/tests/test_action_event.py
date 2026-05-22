"""
Append-only action_event tests.

Verifies that:
  - The action_event table has the correct columns per the design doc.
  - The contact_ops_app role can INSERT into action_event.
  - The contact_ops_app role CANNOT UPDATE or DELETE (append-only enforcement).
  - All expected indexes from migration 0010 exist.
"""

import uuid as _uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

TEST_TENANT = _uuid.UUID("00000000-0000-0000-0000-000000000001")

REQUIRED_COLUMNS: set[str] = {
    "event_id", "event_type", "event_version", "tenant_id",
    "aggregate_type", "aggregate_id", "affected_ids", "payload",
    "actor", "actor_type", "human_authority", "confidence",
    "evidence", "rationale", "status", "proposed_at", "applied_at",
    "approved_by", "reverted_by_event_id", "supersedes_event_id",
    "valid_from", "valid_to", "content_hash", "prev_event_hash",
    "signature",
    # Phase 3 Foundation (migration 0021) additions
    "idempotency_key", "decision_payload", "reversibility_class",
    "parent_proposal_id", "conflict_reason", "agent_version",
    "trust_tier_at_creation", "evidence_pack_id", "trace_id", "span_id",
    "time_to_decide_seconds", "decided_by_user_id", "triggered_by",
}

REQUIRED_INDEXES: set[str] = {
    "ae_tenant_time_idx", "ae_aggregate_idx", "ae_status_idx",
    "ae_event_type_idx", "ae_actor_gin", "ae_evidence_gin",
    "ae_human_authority_idx", "ae_inbox_idx", "ae_low_conf_idx",
    "ae_affected_gin", "ae_supersedes_idx",
}


@pytest.mark.asyncio
async def test_action_event_columns_match_design_doc(db_session: AsyncSession):
    """Every column from the design doc must exist on action_event."""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'action_event'"
        )
    )
    actual = {row[0] for row in result.fetchall()}
    assert REQUIRED_COLUMNS == actual, (
        f"Missing: {REQUIRED_COLUMNS - actual}, "
        f"Extra: {actual - REQUIRED_COLUMNS}"
    )


async def _seed_test_tenant(db_engine):
    """Ensure the test tenant exists (idempotent). Uses a fresh connection."""
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
    async with db_engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,"
                " qdrant_namespace, garage_bucket_prefix) VALUES "
                "(:id, 'test-ae', 'brand', 'AE Test', :owner, 'ae-ns', 'ae-bkt') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": TEST_TENANT, "owner": TEST_TENANT},
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_action_event_insert_as_audit_role_succeeds(
    db_engine, db_session_as_audit_role: AsyncSession,
):
    """contact_ops_audit can INSERT a fully-populated action_event row."""
    await _seed_test_tenant(db_engine)

    event_id = _uuid.uuid4()
    agg_id = _uuid.uuid4()
    payload = '{"op": "test", "data": 42}'
    content_hash = b"\x00" * 32

    await db_session_as_audit_role.execute(
        text(
            """
            INSERT INTO action_event
                (event_id, event_type, event_version, tenant_id,
                 aggregate_type, aggregate_id, payload, actor, actor_type,
                 content_hash)
            VALUES
                (:eid, 'test.insert', 1, :tid,
                 'person', :aid, CAST(:payload AS jsonb),
                 CAST(:actor AS jsonb), 'automation_rule', :hash)
            """
        ),
        {
            "eid": event_id,
            "tid": TEST_TENANT,
            "aid": agg_id,
            "payload": payload,
            "actor": '{"sub": "test-sys"}',
            "hash": content_hash,
        },
    )
    await db_session_as_audit_role.commit()

    result = await db_session_as_audit_role.execute(
        text("SELECT event_id, event_type FROM action_event WHERE event_id = :eid"),
        {"eid": event_id},
    )
    row = result.fetchone()
    assert row is not None
    assert row.event_type == "test.insert"


@pytest.mark.asyncio
async def test_action_event_update_as_app_role_fails(
    db_engine, db_session_as_app_role: AsyncSession,
):
    """contact_ops_app cannot UPDATE action_event — append-only enforcement."""
    await _seed_test_tenant(db_engine)

    event_id = _uuid.uuid4()
    agg_id = _uuid.uuid4()

    await db_session_as_app_role.execute(
        text(
            """
            INSERT INTO action_event
                (event_id, event_type, event_version, tenant_id,
                 aggregate_type, aggregate_id, payload, actor, actor_type,
                 content_hash)
            VALUES
                (:eid, 'test.insert', 1, :tid,
                 'person', :aid, CAST(:payload AS jsonb),
                 CAST(:actor AS jsonb), 'automation_rule', :hash)
            """
        ),
        {"eid": event_id, "tid": TEST_TENANT, "aid": agg_id,
         "payload": "{}", "actor": '{"sub":"test"}',
         "hash": b"\x00" * 32},
    )
    await db_session_as_app_role.commit()

    with pytest.raises(ProgrammingError) as exc_info:
        await db_session_as_app_role.execute(
            text(
                "UPDATE action_event SET event_type = 'modified' WHERE event_id = :eid"
            ),
            {"eid": event_id},
        )
        await db_session_as_app_role.commit()

    assert "42501" in str(exc_info.value) or "permission denied" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_action_event_delete_as_app_role_fails(
    db_engine, db_session_as_app_role: AsyncSession,
):
    """contact_ops_app cannot DELETE from action_event — append-only enforcement."""
    await _seed_test_tenant(db_engine)

    event_id = _uuid.uuid4()
    agg_id = _uuid.uuid4()

    await db_session_as_app_role.execute(
        text(
            """
            INSERT INTO action_event
                (event_id, event_type, event_version, tenant_id,
                 aggregate_type, aggregate_id, payload, actor, actor_type,
                 content_hash)
            VALUES
                (:eid, 'test.insert', 1, :tid,
                 'person', :aid, CAST(:payload AS jsonb),
                 CAST(:actor AS jsonb), 'automation_rule', :hash)
            """
        ),
        {"eid": event_id, "tid": TEST_TENANT, "aid": agg_id,
         "payload": "{}", "actor": '{"sub":"test"}',
         "hash": b"\x00" * 32},
    )
    await db_session_as_app_role.commit()

    with pytest.raises(ProgrammingError) as exc_info:
        await db_session_as_app_role.execute(
            text("DELETE FROM action_event WHERE event_id = :eid"),
            {"eid": event_id},
        )
        await db_session_as_app_role.commit()

    assert "42501" in str(exc_info.value) or "permission denied" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_action_event_indexes_exist(db_session: AsyncSession):
    """All required indexes from migration 0010 exist."""
    result = await db_session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'action_event' AND schemaname = 'public'"
        )
    )
    actual = {row[0] for row in result.fetchall()}
    # Only check our known indexes (the PK index is named differently)
    missing = REQUIRED_INDEXES - actual
    assert not missing, f"Missing indexes: {missing}"
