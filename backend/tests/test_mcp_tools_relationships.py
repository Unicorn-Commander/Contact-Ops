from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

import testcontainers.core.utils

testcontainers.core.utils.raise_for_deprecated_parameter = lambda *_args, **_kwargs: None

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.people import CreatePersonInput, create_person
from contact_ops.mcp.tools.relationships import (
    BulkLinkRelationshipsInput,
    LinkRelationshipInput,
    ListRelationshipsInput,
    SuggestRelationshipsInput,
    UnlinkRelationshipInput,
    bulk_link_relationships,
    link_relationship,
    list_relationships,
    suggest_relationships,
    unlink_relationship,
)


TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
async def _tenant(db_session):
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix)
            VALUES (:id, 'relationships-test', 'personal', 'Relationships Test', :id,
                'relationships-test', 'relationships-test')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": TENANT_ID},
    )
    await db_session.flush()


def ctx(db_session, role: str = "STAFF", scopes: str = "person:read person:write relationship:read relationship:write person:bulk proposal:read") -> MCPContext:
    return MCPContext(
        tenant_id=TENANT_ID,
        user_id=str(TENANT_ID),
        actor_chain={"sub": str(TENANT_ID)},
        human_authority=str(TENANT_ID),
        db=db_session,
        audit_db=db_session,
        request_id="relationships-test",
        claims={"sub": str(TENANT_ID), "realm_access": {"roles": [role]}, "scope": scopes},
    )


async def two_people(db_session):
    c = ctx(db_session)
    a = await create_person(c, CreatePersonInput(display_name="Alice"))
    b = await create_person(c, CreatePersonInput(display_name="Bob"))
    return a.person_id, b.person_id


@pytest.mark.asyncio
async def test_link_list_unlink_relationship_flow(db_session):
    c = ctx(db_session)
    a, b = await two_people(db_session)
    linked = await link_relationship(c, LinkRelationshipInput(from_person_id=a, to_person_id=b, relation_type="mentor"))
    listed = await list_relationships(c, ListRelationshipsInput(person_id=a))
    assert linked.relation_type == "mentor_of"
    assert listed.count >= 1
    ended = await unlink_relationship(c, UnlinkRelationshipInput(edge_id=linked.edge_id))
    assert ended.edge_id == linked.edge_id


@pytest.mark.asyncio
async def test_relationship_rbac_rejects_client_write(db_session):
    c = ctx(db_session, role="CLIENT")
    a, b = await two_people(db_session)
    with pytest.raises(ToolError) as exc:
        await link_relationship(c, LinkRelationshipInput(from_person_id=a, to_person_id=b, relation_type="friend"))
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_suggest_relationships_placeholder(db_session):
    c = ctx(db_session)
    a, _b = await two_people(db_session)
    output = await suggest_relationships(c, SuggestRelationshipsInput(person_id=a))
    assert output.items == []
    assert output.count == 0


@pytest.mark.asyncio
async def test_bulk_link_relationships(db_session):
    c = ctx(db_session)
    a, b = await two_people(db_session)
    output = await bulk_link_relationships(
        c,
        BulkLinkRelationshipsInput(items=[LinkRelationshipInput(from_person_id=a, to_person_id=b, relation_type="colleague")], source_label="test"),
    )
    assert output.created == 1
