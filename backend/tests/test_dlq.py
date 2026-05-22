"""Dead-letter queue tests: park + list + bulk-replay."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from contact_ops.agents.dlq import DeadLetterQueue, ErrorClass
from contact_ops.agents.errors import (
    CostBudgetExceededError,
    RetryableError,
)


class _SchemaErr(Exception):
    """Tagged so ErrorClass.from_exception picks SCHEMA_VALIDATION."""


_SchemaErr.__name__ = "ValidationError"


@pytest_asyncio.fixture
async def _setup(db_session):
    """Seed a tenant + a sentinel action_event that DLQ rows can FK to."""
    tid = "00000000-0000-0000-0000-00000000d001"
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix)
            VALUES (CAST(:id AS uuid), 'dlq-tenant', 'brand', 'DLQ Tenant',
                CAST(:id AS uuid), false, 'dlq-ns', 'dlq-bkt')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": tid},
    )
    # Insert a sentinel action_event we can FK against.
    aid = (
        await db_session.execute(
            text(
                """
                INSERT INTO action_event (
                    event_type, tenant_id, aggregate_type, aggregate_id,
                    payload, actor, actor_type, evidence, status, content_hash,
                    idempotency_key, reversibility_class
                ) VALUES (
                    'test.sentinel', CAST(:t AS uuid), 'person'::entity_kind,
                    gen_random_uuid(),
                    '{}'::jsonb, '{"sub":"test"}'::jsonb, 'agent'::actor_type,
                    '{}'::jsonb, 'proposed'::event_status, '\\xdeadbeef',
                    'test-sentinel-key', 'reversible'
                )
                RETURNING event_id
                """
            ),
            {"t": tid},
        )
    ).scalar_one()
    await db_session.commit()
    return {"tenant_id": uuid.UUID(tid), "event_id": uuid.UUID(str(aid))}


@pytest.mark.asyncio
async def test_park_inserts_row(db_session, _setup):
    dlq = DeadLetterQueue(db=db_session)
    err = RetryableError("downstream 500")
    dlq_id = await dlq.park(
        original_action_event_id=_setup["event_id"],
        agent_slug="dedup",
        tenant_id=_setup["tenant_id"],
        error=err,
        retry_count=3,
        payload={"x": 1},
    )
    await db_session.commit()
    assert isinstance(dlq_id, uuid.UUID)
    row = (
        await db_session.execute(
            text(
                "SELECT agent_slug, retry_count, error_class, replayable "
                "FROM agent_action_dlq WHERE id = CAST(:id AS uuid)"
            ),
            {"id": str(dlq_id)},
        )
    ).mappings().one()
    assert row["agent_slug"] == "dedup"
    assert row["retry_count"] == 3
    assert row["replayable"] is True


def test_error_class_classification():
    assert ErrorClass.from_exception(CostBudgetExceededError(1, "x", agent_slug="t")) == ErrorClass.BUDGET_EXCEEDED
    assert ErrorClass.from_exception(_SchemaErr("nope")) == ErrorClass.SCHEMA_VALIDATION
    assert ErrorClass.from_exception(RuntimeError("nada")) == ErrorClass.OTHER


@pytest.mark.asyncio
async def test_list_by_error_class_filter(db_session, _setup):
    dlq = DeadLetterQueue(db=db_session)
    await dlq.park(
        original_action_event_id=_setup["event_id"],
        agent_slug="a",
        tenant_id=_setup["tenant_id"],
        error=CostBudgetExceededError(5, "monthly", agent_slug="a"),
        retry_count=0,
        payload={},
    )
    await dlq.park(
        original_action_event_id=_setup["event_id"],
        agent_slug="b",
        tenant_id=_setup["tenant_id"],
        error=_SchemaErr("oops"),
        retry_count=0,
        payload={},
    )
    await db_session.commit()
    budget_entries = await dlq.list_by_error_class(
        error_class=ErrorClass.BUDGET_EXCEEDED, limit=10
    )
    assert {e.agent_slug for e in budget_entries} == {"a"}
    schema_entries = await dlq.list_by_error_class(
        error_class=ErrorClass.SCHEMA_VALIDATION, limit=10
    )
    assert {e.agent_slug for e in schema_entries} == {"b"}


@pytest.mark.asyncio
async def test_replay_succeeds_and_marks_resolved(db_session, _setup):
    dlq = DeadLetterQueue(db=db_session)
    dlq_id = await dlq.park(
        original_action_event_id=_setup["event_id"],
        agent_slug="x",
        tenant_id=_setup["tenant_id"],
        error=RetryableError("transient"),
        retry_count=0,
        payload={"k": "v"},
    )
    await db_session.commit()

    replays: list[uuid.UUID] = []

    async def _replay(entry):  # type: ignore[no-untyped-def]
        replays.append(entry.id)

    result = await dlq.replay(dlq_ids=[dlq_id], replay_fn=_replay)
    await db_session.commit()
    assert result.replayed == 1
    assert result.still_failing == 0
    assert replays == [dlq_id]
    resolved_at = (
        await db_session.execute(
            text("SELECT resolved_at FROM agent_action_dlq WHERE id = CAST(:id AS uuid)"),
            {"id": str(dlq_id)},
        )
    ).scalar_one()
    assert resolved_at is not None


@pytest.mark.asyncio
async def test_replay_records_retry_count_on_failure(db_session, _setup):
    dlq = DeadLetterQueue(db=db_session)
    dlq_id = await dlq.park(
        original_action_event_id=_setup["event_id"],
        agent_slug="x",
        tenant_id=_setup["tenant_id"],
        error=RetryableError("still broken"),
        retry_count=2,
        payload={},
    )
    await db_session.commit()

    async def _replay(_entry):  # type: ignore[no-untyped-def]
        raise RuntimeError("still bad")

    result = await dlq.replay(dlq_ids=[dlq_id], replay_fn=_replay)
    await db_session.commit()
    assert result.replayed == 0
    assert result.still_failing == 1
    row = (
        await db_session.execute(
            text(
                "SELECT retry_count, resolved_at FROM agent_action_dlq "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"id": str(dlq_id)},
        )
    ).mappings().one()
    assert row["retry_count"] == 3
    assert row["resolved_at"] is None


@pytest.mark.asyncio
async def test_mark_unreplayable_blocks_future_replays(db_session, _setup):
    dlq = DeadLetterQueue(db=db_session)
    dlq_id = await dlq.park(
        original_action_event_id=_setup["event_id"],
        agent_slug="x",
        tenant_id=_setup["tenant_id"],
        error=RetryableError("permanent"),
        retry_count=5,
        payload={},
    )
    await dlq.mark_unreplayable(entry_id=dlq_id, reason="root cause is bad config")
    await db_session.commit()

    async def _replay(_entry):  # type: ignore[no-untyped-def]
        raise AssertionError("should not be called")

    result = await dlq.replay(dlq_ids=[dlq_id], replay_fn=_replay)
    assert result.skipped_unreplayable == 1
    assert result.replayed == 0
