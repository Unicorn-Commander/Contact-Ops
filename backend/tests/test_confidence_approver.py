from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from contact_ops.services.agents.confidence_approver import run_confidence_approver
from tests.test_dedup_agent import _proposal, _tenant

pytestmark = pytest.mark.asyncio


async def test_confidence_approver_dry_run_lists_candidates(db_session):
    tenant_id = uuid.uuid4()
    await _tenant(db_session, tenant_id)
    await _proposal(db_session, tenant_id, {"display_name": "Auto Apply"})

    result = await run_confidence_approver(db_session, tenant_id=tenant_id, dry_run=True)

    assert result["dry_run"] is True
    assert len(result["candidates"]) == 0


async def test_confidence_approver_respects_tenant_opt_out(db_session):
    tenant_id = uuid.uuid4()
    await _tenant(db_session, tenant_id)
    await db_session.execute(
        text("UPDATE tenants SET auto_approve_disabled = true WHERE id = :id"),
        {"id": tenant_id},
    )

    result = await run_confidence_approver(db_session, tenant_id=tenant_id)

    assert result["disabled"] is True
