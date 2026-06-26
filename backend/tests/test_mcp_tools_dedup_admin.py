from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text
from contact_ops.mcp.errors import HIPAA_BLOCKED, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.dedup_admin import (
    DedupStatusInput,
    ProposeMergeInput,
    _dedup_status,
    _propose_merge,
)

pytestmark = pytest.mark.asyncio


def _ctx(
    db_session,
    *,
    tenant_id: uuid.UUID,
    role: str = "STAFF",
    scopes: str = "person:write dedup:propose dedup:apply dedup:read",
) -> MCPContext:
    return MCPContext(
        tenant_id=tenant_id,
        user_id=str(uuid.uuid4()),
        actor_chain={"sub": "test-staff", "act": {"sub": "system"}},
        human_authority=str(tenant_id),
        db=db_session,
        audit_db=db_session,
        request_id="test-request",
        claims={
            "realm_access": {"roles": [role]},
            "scope": scopes,
        },
    )


async def _seed_person(
    db_session,
    person_id: uuid.UUID,
    tenant_id: uuid.UUID,
    display_name: str = "Test Person",
    given_name: str | None = None,
    family_name: str | None = None,
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
            "name": display_name,
            "gn": given_name or display_name.split()[0] if " " in display_name else display_name,
            "fn": family_name or "",
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


async def test_propose_merge_hipaa_rejected(db_session, seeded_tenants):
    """propose_merge across tenants raises HIPAA_BLOCKED."""
    tenant_id = seeded_tenants["non_hipaa"]
    a_id = uuid.uuid4()
    b_id = uuid.uuid4()

    await _seed_person(db_session, a_id, tenant_id, "Alice Smith")
    await _seed_person(db_session, b_id, tenant_id, "Bob Jones")
    await _seed_email(db_session, a_id, "REMOVED")
    await _seed_email(db_session, b_id, "REMOVED")
    await db_session.commit()

    ctx = _ctx(db_session, tenant_id=tenant_id)

    # Patch crosses_hipaa_fence to simulate cross-HIPAA boundary
    with patch(
        "contact_ops.agents.dedup.hipaa_fence.crosses_hipaa_fence",
        return_value=True,
    ):
        with pytest.raises(ToolError) as exc_info:
            await _propose_merge(
                ctx,
                ProposeMergeInput(
                    person_a_id=a_id,
                    person_b_id=b_id,
                    reason="test merge across HIPAA",
                ),
            )

    assert exc_info.value.code == HIPAA_BLOCKED


async def test_propose_merge_happy_path(db_session, seeded_tenants):
    tenant_id = seeded_tenants["non_hipaa"]
    a_id = uuid.uuid4()
    b_id = uuid.uuid4()

    await _seed_person(db_session, a_id, tenant_id, "Alice Smith", given_name="Alice", family_name="Smith")
    await _seed_person(db_session, b_id, tenant_id, "Bob Jones", given_name="Bob", family_name="Jones")
    await _seed_email(db_session, a_id, "REMOVED")
    await _seed_email(db_session, b_id, "REMOVED")
    await db_session.commit()

    ctx = _ctx(db_session, tenant_id=tenant_id)

    out = await _propose_merge(
        ctx,
        ProposeMergeInput(
            person_a_id=a_id,
            person_b_id=b_id,
            reason="same email address",
        ),
    )

    assert isinstance(out.action_event_id, str) and len(out.action_event_id) > 0
    assert 0.0 <= out.match_probability <= 1.0
    assert out.recommendation in ("auto_merge", "single_review", "batch_review", "discard")

    # Verify the action_event was persisted
    ev = await ctx.audit_db.execute(
        text("SELECT event_type, status FROM action_event WHERE event_id = :eid"),
        {"eid": out.action_event_id},
    )
    ev_row = ev.mappings().first()
    assert ev_row is not None
    assert ev_row["event_type"] == "dedup.propose_merge"
    assert ev_row["status"] == "proposed"


async def test_dedup_status(db_session, seeded_tenants):
    tenant_id = seeded_tenants["non_hipaa"]

    # Seed a propose_merge event so queries return data
    a_id = uuid.uuid4()
    b_id = uuid.uuid4()
    await _seed_person(db_session, a_id, tenant_id, "Alice Smith")
    await _seed_person(db_session, b_id, tenant_id, "Bob Jones")
    await _seed_email(db_session, a_id, "REMOVED")
    await _seed_email(db_session, b_id, "REMOVED")

    await db_session.execute(
        text("""
            INSERT INTO action_event (event_id, event_type, tenant_id,
                aggregate_type, aggregate_id, payload, actor, actor_type,
                evidence, status, content_hash, proposed_at)
            VALUES (:eid, 'dedup.propose_merge', :tid,
                'person', :aid, '{}'::jsonb, '{}'::jsonb, 'agent',
                '{}'::jsonb, 'proposed',
                '\\x0000000000000000000000000000000000000000000000000000000000000000'::bytea,
                now())
        """),
        {
            "eid": str(uuid.uuid4()),
            "tid": str(tenant_id),
            "aid": str(a_id),
        },
    )

    await db_session.commit()

    ctx = _ctx(
        db_session,
        tenant_id=tenant_id,
        role="CLIENT",
        scopes="dedup:read",
    )

    status = await _dedup_status(ctx, DedupStatusInput())

    assert str(status.tenant_id) == str(tenant_id)
    assert status.agent_version == "0.1.0"
    assert status.candidates_evaluated_last_24h >= 0
    assert status.proposals_emitted_last_24h >= 0
    assert status.current_trust_tier is not None
    assert isinstance(status.per_source_fp_rate_30d, dict)
