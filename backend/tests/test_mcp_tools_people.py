from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

import testcontainers.core.utils

testcontainers.core.utils.raise_for_deprecated_parameter = lambda *_args, **_kwargs: None

from contact_ops.mcp.errors import (
    AMBIGUOUS_MATCH,
    INSUFFICIENT_ROLE,
    INSUFFICIENT_SCOPE,
    ToolError,
)
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.people import (
    ArchivePersonInput,
    CreatePersonInput,
    FindPersonByIdentifierInput,
    GetPersonInput,
    IdentityLinkSearchInput,
    IdentifierInput,
    ListPeopleInput,
    ResolvePersonIdInput,
    SearchPeopleInput,
    UpdatePersonInput,
    archive_person,
    bulk_archive_people,
    bulk_upsert_people,
    create_person,
    find_person_by_identifier,
    get_person,
    list_people,
    resolve_person_id,
    search_people,
    search_people_for_identity_link,
    update_person,
    BulkArchivePeopleInput,
    BulkUpsertPeopleInput,
    UpsertPersonInput,
    upsert_person,
)
from contact_ops.models import ActionEvent, Email, Person
from contact_ops.models.enums import MergeStatus


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
async def test_create_person_with_im_handles(db_session):
    c = ctx(db_session)
    created = await create_person(
        c,
        CreatePersonInput(
            display_name="Diesel",
            im_handles=[
                {"protocol": "instagram", "handle": "@diesel", "label": "main"},
                {"protocol": "x", "handle": "diesel"},
            ],
        ),
    )
    fetched = await get_person(
        c, GetPersonInput(person_id=created.person_id, include=["im_handles"])
    )
    handles = {h["handle"]: h for h in (fetched.im_handles or [])}
    assert "@diesel" in handles
    assert handles["@diesel"]["protocol"] == "instagram"
    assert handles["@diesel"]["is_primary"] is True  # first handle auto-primary
    assert "diesel" in handles


@pytest.mark.asyncio
async def test_upsert_person_adds_im_handles_idempotently(db_session):
    c = ctx(db_session)
    first = await create_person(
        c,
        CreatePersonInput(
            display_name="Diesel", emails=[{"address": "REMOVED"}]
        ),
    )
    output = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            im_handles=[{"protocol": "instagram", "handle": "@diesel"}],
        ),
    )
    assert output.action == "updated"
    assert "im_handles" in output.changed_fields
    fetched = await get_person(
        c, GetPersonInput(person_id=first.person_id, include=["im_handles"])
    )
    assert [h["handle"] for h in (fetched.im_handles or [])] == ["@diesel"]
    # Re-upsert with the same handle must not duplicate it.
    again = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            im_handles=[{"protocol": "instagram", "handle": "@diesel"}],
        ),
    )
    assert "im_handles" not in again.changed_fields
    refetched = await get_person(
        c, GetPersonInput(person_id=first.person_id, include=["im_handles"])
    )
    assert len(refetched.im_handles or []) == 1


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
async def test_identity_link_search_is_minimal_lexical_and_explicit(db_session):
    reader = ctx(db_session, role="CLIENT", scopes="person:read")
    writer = ctx(db_session)
    await create_person(
        writer,
        CreatePersonInput(
            display_name="Ada Lovelace",
            bio="This private biography must not cross the federation seam",
            emails=[{"address": "REMOVED", "is_primary": True}],
        ),
    )

    result = await search_people_for_identity_link(
        reader,
        IdentityLinkSearchInput(query="REMOVED"),
    )

    assert result.count == 1
    item = result.items[0]
    assert item.match_confidence == 1.0
    assert item.match_basis == "exact_email"
    assert set(item.model_dump(mode="json")) == {
        "person_id",
        "display_name",
        "primary_email",
        "match_confidence",
        "match_basis",
    }
    serialized = result.model_dump_json()
    assert "embedding" not in serialized
    assert "voice" not in serialized
    assert "biography" not in serialized


@pytest.mark.asyncio
async def test_identity_link_search_requires_person_read_scope(db_session):
    with pytest.raises(ToolError) as exc:
        await search_people_for_identity_link(
            ctx(db_session, role="CLIENT", scopes=""),
            IdentityLinkSearchInput(query="Ada"),
        )
    assert exc.value.code == INSUFFICIENT_SCOPE


@pytest.mark.asyncio
async def test_identity_resolution_follows_merge_and_stays_workspace_bound(db_session):
    reader = ctx(db_session, role="CLIENT", scopes="person:read")
    writer = ctx(db_session)
    survivor = await create_person(writer, CreatePersonInput(display_name="Canonical"))
    stale = await create_person(writer, CreatePersonInput(display_name="Stale"))
    stale_row = await db_session.get(Person, stale.person_id)
    assert stale_row is not None
    stale_row.merge_status = MergeStatus.merged_into
    stale_row.merged_into_id = survivor.person_id
    await db_session.flush()

    resolved = await resolve_person_id(
        reader,
        ResolvePersonIdInput(person_id=stale.person_id),
    )
    assert resolved.status == "redirected"
    assert resolved.canonical_person_id == survivor.person_id
    assert stale.person_id in resolved.alias_ids

    other_tenant = uuid.UUID("00000000-0000-0000-0000-000000000099")
    other_person = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (
                id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix
            ) VALUES (
                :tenant_id, 'other-people-test', 'personal', 'Other',
                :tenant_id, 'other-people-test', 'other-people-test'
            )
            """
        ),
        {"tenant_id": other_tenant},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO persons (
                id, display_name, canonical_owner_tenant_id, merge_status
            ) VALUES (:person_id, 'Other Workspace', :tenant_id, 'canonical')
            """
        ),
        {"person_id": other_person, "tenant_id": other_tenant},
    )
    await db_session.flush()

    denied = await resolve_person_id(
        reader,
        ResolvePersonIdInput(person_id=other_person),
    )
    assert denied.status == "not_found"
    assert denied.canonical_person_id is None


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


@pytest.mark.asyncio
async def test_structured_names_and_optional_handles_roundtrip(db_session):
    from contact_ops.models import IMHandle, Identifier, Url
    c = ctx(db_session)
    created = await create_person(c, CreatePersonInput(display_name="Synthetic Contact"))
    other = await create_person(c, CreatePersonInput(display_name="Other Synthetic"))
    fields = dict(given_name="Synthetic", family_name="Contact", additional_names=["Middle"],
                  nicknames=["Example"], phonetic_given_name="sin-THET-ik", file_as="Contact, Synthetic")
    updated = await update_person(c, UpdatePersonInput(person_id=created.person_id, etag=created.etag, **fields))
    db_session.add_all([
        IMHandle(person_id=created.person_id, protocol="signal", handle="synthetic-handle"),
        Identifier(person_id=created.person_id, namespace="example", value="synthetic-id"),
        Url(person_id=created.person_id, url="https://example.invalid/profile"),
        IMHandle(person_id=other.person_id, protocol="signal", handle="other-handle"),
    ])
    await db_session.flush()
    fetched = await get_person(c, GetPersonInput(person_id=created.person_id))
    assert fetched.etag == updated.etag
    for field, value in fields.items():
        assert getattr(fetched, field) == value
    assert fetched.im_handles is None and fetched.identifiers is None and fetched.urls is None
    included = await get_person(c, GetPersonInput(person_id=created.person_id, include=["im_handles", "identifiers", "urls"]))
    assert all(row["etag"] for rows in (included.im_handles, included.identifiers, included.urls) for row in rows)
    assert [row["handle"] for row in included.im_handles] == ["synthetic-handle"]
    assert [row["value"] for row in included.identifiers] == ["synthetic-id"]
    assert [row["url"] for row in included.urls] == ["https://example.invalid/profile"]
    next_edit = await update_person(c, UpdatePersonInput(person_id=created.person_id, etag=updated.etag, headline="Updated"))
    unchanged = await get_person(c, GetPersonInput(person_id=created.person_id))
    assert unchanged.nicknames == ["Example"] and unchanged.additional_names == ["Middle"]
    await update_person(c, UpdatePersonInput(person_id=created.person_id, etag=next_edit.etag, nicknames=[], additional_names=[]))
    cleared = await get_person(c, GetPersonInput(person_id=created.person_id))
    assert cleared.nicknames == [] and cleared.additional_names == []
