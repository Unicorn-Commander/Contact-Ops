from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from contact_ops.services.agents.quality_filter import run_quality_filter
from tests.test_dedup_agent import _proposal, _tenant

pytestmark = pytest.mark.asyncio


async def test_quality_filter_archives_empty_identity(db_session):
    tenant_id = uuid.uuid4()
    await _tenant(db_session, tenant_id)
    event_id = await _proposal(
        db_session,
        tenant_id,
        {"display_name": "", "emails": [], "phones": []},
    )

    result = await run_quality_filter(db_session, tenant_id=tenant_id)

    assert result["archived_count"] == 1
    status = await db_session.scalar(
        text("SELECT status FROM action_event WHERE event_id = :id"),
        {"id": event_id},
    )
    assert status == "resolved"


async def test_quality_filter_dry_run_does_not_archive_human_contact(db_session):
    tenant_id = uuid.uuid4()
    await _tenant(db_session, tenant_id)
    event_id = await _proposal(
        db_session,
        tenant_id,
        {"display_name": "Aaron Stransky", "emails": [{"address": "REMOVED"}]},
    )

    result = await run_quality_filter(db_session, tenant_id=tenant_id, dry_run=True)

    assert result["candidates"] == []
    status = await db_session.scalar(
        text("SELECT status FROM action_event WHERE event_id = :id"),
        {"id": event_id},
    )
    assert status == "proposed"
