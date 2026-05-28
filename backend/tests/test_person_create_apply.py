from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.services.inbox_mutations import InboxMutationError, approve_proposal

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
REVIEWER_ID = uuid.UUID("00000000-0000-0000-0000-000000000011")


@pytest.fixture(autouse=True)
async def _tenant(db_session: AsyncSession) -> None:
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix)
            VALUES (:id, 'apply-test', 'personal', 'Apply Test', :id,
                'apply-test', 'apply-test')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": TENANT_ID},
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_person_create_approval_materializes_person_email_phone(
    db_session: AsyncSession,
) -> None:
    person_id = uuid.uuid4()
    proposal_id = await _insert_person_create_proposal(db_session, person_id=person_id)

    result = await approve_proposal(
        db=db_session,
        app_db=db_session,
        tenant_id=TENANT_ID,
        reviewer_id=REVIEWER_ID,
        proposal_id=proposal_id,
        tier_assigned=3,
        typed_phrase=None,
        field_choices=None,
        custom_values=None,
        time_to_decide_sec=3,
        keyboard_path=False,
        typed_phrase_used=False,
    )

    assert result["applied"] is True
    person = await db_session.execute(
        text("SELECT display_name FROM persons WHERE id = CAST(:id AS uuid)"),
        {"id": str(person_id)},
    )
    assert person.scalar_one() == "Jane Import"
    email = await db_session.scalar(
        text("SELECT address FROM emails WHERE person_id = CAST(:id AS uuid)"),
        {"id": str(person_id)},
    )
    phone = await db_session.scalar(
        text("SELECT e164 FROM phones WHERE person_id = CAST(:id AS uuid)"),
        {"id": str(person_id)},
    )
    status = await db_session.scalar(
        text("SELECT status FROM action_event WHERE event_id = CAST(:id AS uuid)"),
        {"id": str(proposal_id)},
    )
    assert email == "REMOVED"
    assert phone == "+14155550100"
    assert status == "applied"


@pytest.mark.asyncio
async def test_person_create_approval_is_idempotent(db_session: AsyncSession) -> None:
    person_id = uuid.uuid4()
    proposal_id = await _insert_person_create_proposal(db_session, person_id=person_id)
    await approve_proposal(
        db=db_session,
        app_db=db_session,
        tenant_id=TENANT_ID,
        reviewer_id=REVIEWER_ID,
        proposal_id=proposal_id,
        tier_assigned=3,
        typed_phrase=None,
        field_choices=None,
        custom_values=None,
        time_to_decide_sec=None,
        keyboard_path=False,
        typed_phrase_used=False,
    )
    await db_session.execute(
        text("UPDATE action_event SET status='proposed' WHERE event_id = CAST(:id AS uuid)"),
        {"id": str(proposal_id)},
    )
    await approve_proposal(
        db=db_session,
        app_db=db_session,
        tenant_id=TENANT_ID,
        reviewer_id=REVIEWER_ID,
        proposal_id=proposal_id,
        tier_assigned=3,
        typed_phrase=None,
        field_choices=None,
        custom_values=None,
        time_to_decide_sec=None,
        keyboard_path=False,
        typed_phrase_used=False,
    )
    count = await db_session.scalar(
        text("SELECT count(*) FROM persons WHERE id = CAST(:id AS uuid)"),
        {"id": str(person_id)},
    )
    assert count == 1


@pytest.mark.asyncio
async def test_person_create_approval_tenant_isolated(db_session: AsyncSession) -> None:
    proposal_id = await _insert_person_create_proposal(db_session, person_id=uuid.uuid4())
    with pytest.raises(InboxMutationError):
        await approve_proposal(
            db=db_session,
            app_db=db_session,
            tenant_id=uuid.uuid4(),
            reviewer_id=REVIEWER_ID,
            proposal_id=proposal_id,
            tier_assigned=2,
            typed_phrase=None,
            field_choices=None,
            custom_values=None,
            time_to_decide_sec=None,
            keyboard_path=False,
            typed_phrase_used=False,
        )


async def _insert_person_create_proposal(
    db: AsyncSession,
    *,
    person_id: uuid.UUID,
) -> uuid.UUID:
    proposal_id = uuid.uuid4()
    payload_after = {
        "person_id": str(person_id),
        "display_name": "Jane Import",
        "given_name": "Jane",
        "family_name": "Import",
        "emails": [{"address": "REMOVED", "type": "work", "is_primary": True}],
        "phones": [{"e164": "+14155550100", "type": "mobile", "is_primary": True}],
        "tags": ["imported"],
        "source": {"action": "test"},
    }
    decision_payload = {"payload_before": None, "payload_after": payload_after}
    content_hash = hashlib.sha256(json.dumps(payload_after, sort_keys=True).encode()).digest()
    await db.execute(
        text(
            """
            INSERT INTO action_event (
                event_id, event_type, event_version, tenant_id, aggregate_type, aggregate_id,
                payload, actor, actor_type, confidence, evidence, rationale, status,
                content_hash, decision_payload, reversibility_class, trust_tier_at_creation
            ) VALUES (
                CAST(:event_id AS uuid), 'person.create', 1, CAST(:tenant_id AS uuid),
                'person'::entity_kind, CAST(:person_id AS uuid),
                CAST(:payload AS jsonb), CAST(:actor AS jsonb), 'agent'::actor_type,
                0.9, '{}'::jsonb, 'test', 'proposed'::event_status, :content_hash,
                CAST(:decision_payload AS jsonb), 'reversible', 2
            )
            """
        ),
        {
            "event_id": str(proposal_id),
            "tenant_id": str(TENANT_ID),
            "person_id": str(person_id),
            "payload": json.dumps({"before": None, "after": payload_after}),
            "actor": json.dumps({"sub": "test-agent", "act": {"sub": "system"}}),
            "content_hash": content_hash,
            "decision_payload": json.dumps(decision_payload),
        },
    )
    return proposal_id
