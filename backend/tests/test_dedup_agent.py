from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import text

from contact_ops.services.agents.dedup_agent import run_dedup_agent

pytestmark = pytest.mark.asyncio


async def test_dedup_agent_merges_duplicate_person_create_proposals(db_session):
    tenant_id = uuid.uuid4()
    await _tenant(db_session, tenant_id)
    events = [
        await _proposal(
            db_session,
            tenant_id,
            {
                "display_name": "Aaron",
                "emails": [{"address": "REMOVED"}],
                "phones": [{"e164": "+15551231234"}],
            },
        ),
        await _proposal(
            db_session,
            tenant_id,
            {
                "display_name": "aaron",
                "emails": [{"address": "REMOVED"}],
                "phones": [{"e164": "+15551231234"}],
            },
        ),
        await _proposal(
            db_session,
            tenant_id,
            {
                "display_name": "Aaron",
                "emails": [{"address": "REMOVED"}],
                "phones": [{"e164": "+15551231234"}],
            },
        ),
        await _proposal(
            db_session,
            tenant_id,
            {"display_name": "Other", "emails": [{"address": "REMOVED"}]},
        ),
    ]

    result = await run_dedup_agent(db_session, tenant_id=tenant_id)

    assert result["clusters_merged"] == 1
    rows = (
        await db_session.execute(
            text(
                """
                SELECT event_id, status
                FROM action_event
                WHERE event_id = ANY(CAST(:ids AS uuid[]))
                """
            ),
            {"ids": [str(item) for item in events]},
        )
    ).mappings().all()
    statuses = {row["event_id"]: row["status"] for row in rows}
    assert list(statuses.values()).count("proposed") == 2
    assert list(statuses.values()).count("resolved") == 2


async def _tenant(db, tenant_id: uuid.UUID) -> None:
    await db.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix)
            VALUES (:id, :slug, 'brand', 'Tenant', :id, :slug, :slug)
            """
        ),
        {"id": tenant_id, "slug": f"t-{tenant_id.hex[:8]}"},
    )


async def _proposal(db, tenant_id: uuid.UUID, payload_after: dict) -> uuid.UUID:
    event_id = uuid.uuid4()
    aggregate_id = uuid.uuid4()
    payload = {"before": None, "after": payload_after}
    await db.execute(
        text(
            """
            INSERT INTO action_event (
                event_id, tenant_id, event_type, aggregate_type, aggregate_id,
                payload, actor, actor_type, confidence, status, content_hash,
                decision_payload, reversibility_class
            ) VALUES (
                :event_id, :tenant_id, 'person.create', 'person', :aggregate_id,
                CAST(:payload AS jsonb), '{"agent":"connector"}', 'agent', 0.9,
                'proposed', :hash, CAST(:decision_payload AS jsonb), 'reversible'
            )
            """
        ),
        {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "aggregate_id": aggregate_id,
            "payload": json.dumps(payload),
            "hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest(),
            "decision_payload": json.dumps(
                {"payload_before": None, "payload_after": payload_after}
            ),
        },
    )
    return event_id
