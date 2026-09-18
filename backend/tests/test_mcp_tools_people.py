from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

import testcontainers.core.utils

testcontainers.core.utils.raise_for_deprecated_parameter = lambda *_args, **_kwargs: None

from contact_ops.mcp.errors import AMBIGUOUS_MATCH, INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.people import (
    ArchivePersonInput,
    CreatePersonInput,
    FindPersonByIdentifierInput,
    GetPersonInput,
    IdentifierInput,
    ListPeopleInput,
    SearchPeopleInput,
    UpdatePersonInput,
    archive_person,
    bulk_archive_people,
    bulk_upsert_people,
    create_person,
    find_person_by_identifier,
    get_person,
    list_people,
    search_people,
    update_person,
    BulkArchivePeopleInput,
    BulkUpsertPeopleInput,
    UpsertPersonInput,
    upsert_person,
)
from contact_ops.models import ActionEvent, Email


TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
async def _tenant(db_session):
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix)
            VALUES (:id, 'people-test', 'personal', 'People Test', :id,
                'people-test', 'people-test')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": TENANT_ID},
    )
    await db_session.flush()


def ctx(db_session, role: str = "STAFF", scopes: str = "person:read person:write person:archive person:bulk person:delete") -> MCPContext:
    return MCPContext(
        tenant_id=TENANT_ID,
        user_id=str(TENANT_ID),
        actor_chain={"sub": str(TENANT_ID)},
        human_authority=str(TENANT_ID),
        db=db_session,
        audit_db=db_session,
        request_id="people-test",
        claims={"sub": str(TENANT_ID), "realm_access": {"roles": [role]}, "scope": scopes},
    )


@pytest.mark.asyncio
async def test_create_update_get_person_flow_and_audit(db_session):
    before = await db_session.scalar(select(__import__("sqlalchemy").func.count()).select_from(ActionEvent))
    c = ctx(db_session)
    created = await create_person(
        c,
        CreatePersonInput(display_name="Ada Lovelace", given_name="Ada", emails=[{"address": "REMOVED", "type": "work", "is_primary": True}]),
    )
    updated = await update_person(c, UpdatePersonInput(person_id=created.person_id, etag=created.etag, headline="Mathematician"))
    fetched = await get_person(c, GetPersonInput(person_id=created.person_id, include=["emails"]))
    assert fetched.display_name == "Ada Lovelace"
    assert fetched.emails and fetched.emails[0]["address"] == "REMOVED"
    assert updated.changed_fields == ["headline"]
    count = await db_session.scalar(select(__import__("sqlalchemy").func.count()).select_from(ActionEvent))
    assert count - before == 2


@pytest.mark.asyncio
async def test_create_person_rbac_rejects_client(db_session):
    with pytest.raises(ToolError) as exc:
        await create_person(ctx(db_session, role="CLIENT"), CreatePersonInput(display_name="Nope"))
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_create_person_idempotency_returns_same_person(db_session):
    c = ctx(db_session)
    key = uuid.uuid4()
    args = CreatePersonInput(display_name="Grace Hopper", idempotency_key=key)
    first = await create_person(c, args)
    second = await create_person(c, args)
    assert second.person_id == first.person_id


@pytest.mark.asyncio
async def test_upsert_person_updates_existing_by_email(db_session):
    c = ctx(db_session)
    first = await create_person(c, CreatePersonInput(display_name="Original", emails=[{"address": "REMOVED"}]))
    output = await upsert_person(
        c,
        UpsertPersonInput(match_by=[IdentifierInput(namespace="email", value="REMOVED")], display_name="Updated", conflict_strategy="prefer_incoming"),
    )
    assert output.action == "updated"
    assert output.person_id == first.person_id


@pytest.mark.asyncio
async def test_list_and_search_people(db_session):
    c = ctx(db_session, role="CLIENT", scopes="person:read")
    writer = ctx(db_session)
    await create_person(writer, CreatePersonInput(display_name="Filter Target", emails=[{"address": "REMOVED"}]))
    listed = await list_people(c, ListPeopleInput(has_email=True))
    searched = await search_people(c, SearchPeopleInput(query="Filter"))
    assert listed.count >= 1
    assert any(item.display_name == "Filter Target" for item in searched.items)


@pytest.mark.asyncio
async def test_find_person_by_identifier_ambiguous(db_session):
    c = ctx(db_session)
    p1 = await create_person(c, CreatePersonInput(display_name="One"))
    p2 = await create_person(c, CreatePersonInput(display_name="Two"))
    db_session.add_all([Email(person_id=p1.person_id, address="REMOVED"), Email(person_id=p2.person_id, address="REMOVED")])
    await db_session.flush()
    with pytest.raises(ToolError) as exc:
        await find_person_by_identifier(c, FindPersonByIdentifierInput(identifiers=[IdentifierInput(namespace="email", value="REMOVED")]))
    assert exc.value.code == AMBIGUOUS_MATCH


@pytest.mark.asyncio
async def test_bulk_people_tools(db_session):
    c = ctx(db_session, role="MANAGER")
    output = await bulk_upsert_people(
        c,
        BulkUpsertPeopleInput(
            items=[UpsertPersonInput(match_by=[IdentifierInput(namespace="email", value="REMOVED")], display_name="Bulk")],
            source_label="test",
        ),
    )
    assert output.created == 1
    archived = await bulk_archive_people(c, BulkArchivePeopleInput(person_ids=[output.results[0].person_id], reason="other"))
    assert archived.archived == 1
