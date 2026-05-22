"""Outbox publish + claim/sweep + LISTEN/NOTIFY tests.

Covers the durability backstop: even if no LISTEN connection is up,
``claim_batch`` will pick up the row once it ages past ``min_age_seconds``,
guaranteeing every published event is eventually handled.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents.outbox import EventOutbox, OutboxMessage


@pytest_asyncio.fixture
async def _tenant(db_session):
    tid = "00000000-0000-0000-0000-00000000c001"
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix)
            VALUES (CAST(:id AS uuid), 'outbox-tenant', 'brand', 'Outbox Tenant',
                CAST(:id AS uuid), false, 'outbox-ns', 'outbox-bkt')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": tid},
    )
    await db_session.commit()
    return uuid.UUID(tid)


@pytest.mark.asyncio
async def test_publish_inserts_row(db_session, _tenant):
    outbox = EventOutbox(engine=db_session.bind.engine)
    outbox_id = await outbox.publish(
        db=db_session,
        channel="contacts.inserted",
        payload={"contact_id": "abc"},
        tenant_id=_tenant,
    )
    await db_session.commit()
    assert outbox_id > 0
    row = (
        await db_session.execute(
            text(
                "SELECT channel, processed_at, payload "
                "FROM event_outbox WHERE id = :id"
            ),
            {"id": outbox_id},
        )
    ).mappings().one()
    assert row["channel"] == "contacts.inserted"
    assert row["processed_at"] is None
    assert row["payload"]["contact_id"] == "abc"


@pytest.mark.asyncio
async def test_claim_batch_min_age_filters_recent(db_session, _tenant):
    """A just-published row should NOT be claimed when min_age_seconds=30."""
    outbox = EventOutbox(engine=db_session.bind.engine)
    await outbox.publish(
        db=db_session,
        channel="test.fresh",
        payload={"x": 1},
        tenant_id=_tenant,
    )
    await db_session.commit()
    claimed = await outbox.claim_batch(
        db=db_session,
        channel="test.fresh",
        consumer_id="t",
        min_age_seconds=30,
    )
    assert claimed == []


@pytest.mark.asyncio
async def test_claim_batch_picks_up_aged(db_session, _tenant):
    """With min_age_seconds=0, claim_batch returns the row immediately."""
    outbox = EventOutbox(engine=db_session.bind.engine)
    await outbox.publish(
        db=db_session,
        channel="test.aged",
        payload={"k": "v"},
        tenant_id=_tenant,
    )
    await db_session.commit()
    claimed = await outbox.claim_batch(
        db=db_session,
        channel="test.aged",
        consumer_id="t",
        min_age_seconds=0,
    )
    assert len(claimed) == 1
    msg = claimed[0]
    assert isinstance(msg, OutboxMessage)
    assert msg.channel == "test.aged"
    assert msg.payload == {"k": "v"}
    assert msg.tenant_id == _tenant


@pytest.mark.asyncio
async def test_mark_processed_sets_timestamp(db_session, _tenant):
    outbox = EventOutbox(engine=db_session.bind.engine)
    outbox_id = await outbox.publish(
        db=db_session,
        channel="test.mark",
        payload={},
        tenant_id=_tenant,
    )
    await db_session.commit()
    await outbox.mark_processed(db=db_session, outbox_id=outbox_id)
    await db_session.commit()
    processed_at = (
        await db_session.execute(
            text("SELECT processed_at FROM event_outbox WHERE id = :id"),
            {"id": outbox_id},
        )
    ).scalar_one()
    assert processed_at is not None


@pytest.mark.asyncio
async def test_claim_batch_skips_processed(db_session, _tenant):
    """A row whose processed_at is set must not be re-claimed."""
    outbox = EventOutbox(engine=db_session.bind.engine)
    outbox_id = await outbox.publish(
        db=db_session,
        channel="test.skip",
        payload={},
        tenant_id=_tenant,
    )
    await db_session.commit()
    await outbox.mark_processed(db=db_session, outbox_id=outbox_id)
    await db_session.commit()
    claimed = await outbox.claim_batch(
        db=db_session,
        channel="test.skip",
        consumer_id="t",
        min_age_seconds=0,
    )
    assert claimed == []


@pytest.mark.asyncio
async def test_claim_batch_for_update_skip_locked_concurrency(db_engine):
    """Two concurrent claim_batch() calls must not double-claim the same row.

    Seeds its own tenant via a committed session because the rollback-scoped
    ``_tenant`` fixture would not be visible to the separate sessions used
    by the concurrent claimers.
    """
    outbox = EventOutbox(engine=db_engine)
    tid = "00000000-0000-0000-0000-00000000cc01"
    tenant_uuid = uuid.UUID(tid)

    async with AsyncSession(db_engine) as setup_session:
        await setup_session.execute(
            text(
                """
                INSERT INTO tenants (id, slug, kind, display_name,
                    owner_user_id, hipaa_mode, qdrant_namespace,
                    garage_bucket_prefix)
                VALUES (CAST(:id AS uuid), 'outbox-concur', 'brand',
                    'Outbox Concur', CAST(:id AS uuid), false,
                    'oc-ns', 'oc-bkt')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": tid},
        )
        outbox_id = await outbox.publish(
            db=setup_session,
            channel="test.concurrent",
            payload={},
            tenant_id=tenant_uuid,
        )
        await setup_session.commit()

    async def _claim() -> list:
        async with AsyncSession(db_engine) as s:
            msgs = await outbox.claim_batch(
                db=s,
                channel="test.concurrent",
                consumer_id="t",
                min_age_seconds=0,
            )
            await s.commit()
            return msgs

    results = await asyncio.gather(_claim(), _claim())
    total_claimed = sum(len(r) for r in results)
    assert total_claimed == 1

    async with AsyncSession(db_engine) as cleanup:
        await cleanup.execute(
            text("DELETE FROM event_outbox WHERE id = :id"),
            {"id": outbox_id},
        )
        await cleanup.execute(
            text("DELETE FROM tenants WHERE id = CAST(:id AS uuid)"),
            {"id": tid},
        )
        await cleanup.commit()


@pytest.mark.asyncio
async def test_payload_with_uuid_serializes(db_session, _tenant):
    """UUIDs in payload must be JSON-serializable via the custom default."""
    outbox = EventOutbox(engine=db_session.bind.engine)
    aggregate_id = uuid.uuid4()
    outbox_id = await outbox.publish(
        db=db_session,
        channel="test.uuid",
        payload={"aggregate_id": aggregate_id},
        tenant_id=_tenant,
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT payload FROM event_outbox WHERE id = :id"),
            {"id": outbox_id},
        )
    ).mappings().one()
    assert row["payload"]["aggregate_id"] == str(aggregate_id)
