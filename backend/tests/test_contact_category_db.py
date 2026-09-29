"""Category collision and backfill against the real Postgres schema.

These tests use the suite's db_session fixture (Alembic-migrated Postgres).
They are not a stand-in for a mocked schema. The lane fence cannot start that
database; the orchestrator runs this module.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from contact_ops.mcp.errors import INSUFFICIENT_SCOPE, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.people import (
    CreatePersonInput,
    GetPersonInput,
    IdentifierInput,
    SearchPeopleInput,
    UpsertPersonInput,
    create_person,
    get_person,
    search_people,
    upsert_person,
)
from contact_ops.services.contact_category import READ_PERSONAL_SCOPE

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0053_contact_category.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("contact_category_0053", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
async def _tenant(db_session):
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix)
            VALUES (:id, 'category-test', 'personal', 'Category Test', :id,
                'category-test', 'category-test')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": TENANT_ID},
    )
    await db_session.flush()


def _ctx(db_session, claims: dict, actor_chain: dict | None = None) -> MCPContext:
    return MCPContext(
        tenant_id=TENANT_ID,
        user_id=str(TENANT_ID),
        actor_chain=actor_chain or {"sub": str(TENANT_ID)},
        human_authority=str(TENANT_ID),
        db=db_session,
        audit_db=db_session,
        request_id="category-test",
        claims=claims,
    )


def human_ctx(db_session) -> MCPContext:
    # Same issuer the app treats as a human session, including the test default.
    from contact_ops.core.config import get_settings

    issuer = (get_settings().KEYCLOAK_ISSUER or "").rstrip("/")
    return _ctx(
        db_session,
        {
            "sub": str(TENANT_ID),
            "iss": issuer,
            "realm_access": {"roles": ["STAFF"]},
            "scope": "person:read person:write",
        },
    )


def service_ctx(db_session, scopes: str = "person:read person:write") -> MCPContext:
    claims = {
        "sub": "customer-ops",
        "iss": "https://brigade.example",
        "brigade_claims": {"sub": "customer-ops"},
        "preferred_username": "service-account-customer-ops",
        "realm_access": {"roles": ["STAFF"]},
        "scope": scopes,
    }
    return _ctx(
        db_session,
        claims,
        actor_chain={"sub": "customer-ops", "act": {"sub": "customer-ops"}},
    )


async def _category(db_session, person_id: uuid.UUID) -> str:
    return await db_session.scalar(
        text("SELECT category::text FROM persons WHERE id = :id"),
        {"id": person_id},
    )


async def _linked(db_session, left: uuid.UUID, right: uuid.UUID) -> bool:
    row = await db_session.scalar(
        text(
            """
            SELECT 1 FROM person_person_relation
            WHERE relation_type = 'suspected_same_as'
              AND tenant_visibility = :tenant
              AND (
                (from_person_id = :left AND to_person_id = :right)
                OR (from_person_id = :right AND to_person_id = :left)
              )
            """
        ),
        {"tenant": TENANT_ID, "left": left, "right": right},
    )
    return row is not None


async def _open_review(db_session, left: uuid.UUID, right: uuid.UUID) -> str | None:
    return await db_session.scalar(
        text(
            """
            SELECT status::text
            FROM action_event
            WHERE event_type = 'person.possible_same_person'
              AND payload->'after'->>'person_a_id' IN (:left, :right)
              AND payload->'after'->>'person_b_id' IN (:left, :right)
            ORDER BY proposed_at DESC
            LIMIT 1
            """
        ),
        {"left": str(left), "right": str(right)},
    )


@pytest.mark.asyncio
async def test_prospect_email_does_not_overwrite_personal(db_session):
    human = human_ctx(db_session)
    personal = await create_person(
        human,
        CreatePersonInput(
            display_name="Owner Network",
            emails=[{"address": "REMOVED"}],
        ),
    )
    created = await upsert_person(
        service_ctx(db_session),
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            display_name="Sourced Lead",
            emails=[{"address": "REMOVED"}],
        ),
    )
    assert created.action == "created"
    assert created.person_id != personal.person_id
    still = await get_person(human, GetPersonInput(person_id=personal.person_id))
    assert still.display_name == "Owner Network"
    assert still.category == "PERSONAL"
    prospect = await get_person(human, GetPersonInput(person_id=created.person_id))
    assert prospect.display_name == "Sourced Lead"
    assert prospect.category == "PROSPECT"
    assert await _linked(db_session, personal.person_id, created.person_id)
    assert await _open_review(db_session, personal.person_id, created.person_id) == "proposed"


@pytest.mark.asyncio
async def test_personal_phone_does_not_overwrite_prospect(db_session):
    service = service_ctx(db_session)
    prospect = await create_person(
        service,
        CreatePersonInput(
            display_name="Sourced Lead",
            phones=[{"e164": "+15555550100"}],
        ),
    )
    assert await _category(db_session, prospect.person_id) == "PROSPECT"
    created = await upsert_person(
        human_ctx(db_session),
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="phone", value="+15555550100")],
            display_name="Owner Network",
            phones=[{"e164": "+15555550100"}],
        ),
    )
    assert created.action == "created"
    assert created.person_id != prospect.person_id
    still = await get_person(service, GetPersonInput(person_id=prospect.person_id))
    assert still.display_name == "Sourced Lead"
    assert still.category == "PROSPECT"
    owner = await get_person(human_ctx(db_session), GetPersonInput(person_id=created.person_id))
    assert owner.category == "PERSONAL"
    assert await _linked(db_session, prospect.person_id, created.person_id)
    assert await _open_review(db_session, prospect.person_id, created.person_id) == "proposed"


@pytest.mark.asyncio
async def test_same_category_email_upsert_still_updates(db_session):
    service = service_ctx(db_session)
    first = await create_person(
        service,
        CreatePersonInput(
            display_name="Original Lead",
            emails=[{"address": "REMOVED"}],
        ),
    )
    updated = await upsert_person(
        service,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            display_name="Renamed Lead",
            conflict_strategy="prefer_incoming",
        ),
    )
    assert updated.action == "updated"
    assert updated.person_id == first.person_id
    fetched = await get_person(service, GetPersonInput(person_id=first.person_id))
    assert fetched.display_name == "Renamed Lead"


@pytest.mark.asyncio
async def test_display_name_collision_links_both_directions(db_session):
    human = human_ctx(db_session)
    service = service_ctx(db_session)
    personal = await create_person(human, CreatePersonInput(display_name="  Same   Name "))
    prospect = await create_person(service, CreatePersonInput(display_name="same name"))
    assert personal.person_id != prospect.person_id
    assert await _linked(db_session, personal.person_id, prospect.person_id)
    untouched = await get_person(human, GetPersonInput(person_id=personal.person_id))
    assert untouched.display_name == "  Same   Name "

    later_prospect = await create_person(
        service, CreatePersonInput(display_name="Other Lead")
    )
    later_personal = await create_person(
        human, CreatePersonInput(display_name="other lead")
    )
    assert later_personal.person_id != later_prospect.person_id
    assert await _linked(db_session, later_prospect.person_id, later_personal.person_id)
    kept = await get_person(service, GetPersonInput(person_id=later_prospect.person_id))
    assert kept.display_name == "Other Lead"


@pytest.mark.asyncio
async def test_service_list_hides_personal_until_scope_is_explicit(db_session):
    human = human_ctx(db_session)
    service = service_ctx(db_session)
    await create_person(human, CreatePersonInput(display_name="Owner Only"))
    await create_person(service, CreatePersonInput(display_name="Lead Only"))

    everyone = await search_people(human, SearchPeopleInput(limit=50))
    assert {item.category for item in everyone.items} >= {"PERSONAL", "PROSPECT"}

    leads = await search_people(service, SearchPeopleInput(limit=50))
    assert leads.count >= 1
    assert {item.category for item in leads.items} == {"PROSPECT"}

    with pytest.raises(ToolError) as exc:
        await search_people(service, SearchPeopleInput(category="PERSONAL"))
    assert exc.value.code == INSUFFICIENT_SCOPE

    privileged = service_ctx(
        db_session, scopes=f"person:read person:write {READ_PERSONAL_SCOPE}"
    )
    personal_only = await search_people(
        privileged, SearchPeopleInput(category="PERSONAL", limit=50)
    )
    assert personal_only.count >= 1
    assert {item.category for item in personal_only.items} == {"PERSONAL"}


@pytest.mark.asyncio
async def test_service_cannot_write_personal_without_the_scope(db_session):
    human = human_ctx(db_session)
    personal = await create_person(
        human,
        CreatePersonInput(
            display_name="Owner Network",
            emails=[{"address": "REMOVED"}],
        ),
    )
    with pytest.raises(ToolError) as exc:
        await upsert_person(
            service_ctx(db_session),
            UpsertPersonInput(
                match_by=[IdentifierInput(namespace="email", value="REMOVED")],
                display_name="Should Not Land",
                category="PERSONAL",
            ),
        )
    assert exc.value.code == INSUFFICIENT_SCOPE
    still = await get_person(human, GetPersonInput(person_id=personal.person_id))
    assert still.display_name == "Owner Network"


@pytest.mark.asyncio
async def test_sourced_prospect_backfill_is_idempotent_and_reversible(db_session):
    migration = _migration()
    human = human_ctx(db_session)
    service = service_ctx(db_session)
    tagged = await create_person(human, CreatePersonInput(display_name="Tagged Cohort"))
    plain = await create_person(human, CreatePersonInput(display_name="Address Book"))
    already = await create_person(
        service,
        CreatePersonInput(display_name="Already Prospect", initial_tags=["sourced-prospect"]),
    )
    await db_session.execute(
        text(
            """
            UPDATE person_tenant_membership
            SET tags = ARRAY['sourced-prospect']
            WHERE person_id = :person_id
            """
        ),
        {"person_id": tagged.person_id},
    )
    await db_session.execute(
        text("UPDATE persons SET category = 'PERSONAL' WHERE id = :person_id"),
        {"person_id": tagged.person_id},
    )
    await db_session.flush()

    first = await db_session.execute(text(migration.BACKFILL_PROSPECT_SQL))
    assert first.rowcount == 1
    assert await _category(db_session, tagged.person_id) == "PROSPECT"
    assert await _category(db_session, plain.person_id) == "PERSONAL"
    assert await _category(db_session, already.person_id) == "PROSPECT"

    second = await db_session.execute(text(migration.BACKFILL_PROSPECT_SQL))
    assert second.rowcount == 0
    assert await _category(db_session, tagged.person_id) == "PROSPECT"

    restored = await db_session.execute(
        text(migration.RESTORE_PERSONAL_FOR_SOURCED_PROSPECT_SQL)
    )
    assert await _category(db_session, tagged.person_id) == "PERSONAL"
    assert await _category(db_session, already.person_id) == "PERSONAL"
    assert await _category(db_session, plain.person_id) == "PERSONAL"
    assert restored.rowcount == 2
