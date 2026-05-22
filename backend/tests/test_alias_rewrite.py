"""
Alias rewrite trigger tests.

Verify that the rewrite_alias_person_id() trigger correctly rewrites
person_id through the person_alias table during merges.
"""

import pytest
import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def db_session() -> AsyncSession:
    from contact_ops.core.database import async_session_maker
    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def test_data(db_session):
    """Create test data: two persons, one email, one alias."""
    import uuid

    person_1_id = uuid.uuid4()
    person_2_id = uuid.uuid4()
    alias_id = uuid.uuid4()
    email_id = uuid.uuid4()
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    # Create tenant
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.tenants (id, slug, kind, display_name, owner_user_id, qdrant_namespace)
            VALUES (:id, :slug, 'personal', :name, :owner, :ns)
        """),
        {"id": tenant_id, "slug": "test-tenant", "name": "Test Tenant",
         "owner": tenant_id, "ns": "test-ns"},
    )

    # Create two persons
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.persons (id, display_name, canonical_owner_tenant_id)
            VALUES (:id, :name, :tenant_id)
        """),
        {"id": person_1_id, "name": "Person One", "tenant_id": tenant_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.persons (id, display_name, canonical_owner_tenant_id)
            VALUES (:id, :name, :tenant_id)
        """),
        {"id": person_2_id, "name": "Person Two", "tenant_id": tenant_id},
    )

    # Create alias (person_1 merged into person_2)
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.person_alias (id, alias_person_id, canonical_person_id)
            VALUES (:id, :alias, :canonical)
        """),
        {"id": alias_id, "alias": person_1_id, "canonical": person_2_id},
    )

    # Create email for person_1
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.emails (id, person_id, email_address, kind)
            VALUES (:id, :person_id, :email, 'work')
        """),
        {"id": email_id, "person_id": person_1_id, "email": "REMOVED"},
    )

    await db_session.commit()
    return {
        "person_1_id": person_1_id,
        "person_2_id": person_2_id,
        "email_id": email_id,
        "tenant_id": tenant_id,
    }


@pytest.mark.asyncio
async def test_alias_rewrite_on_email_update(db_session, test_data):
    """Verify email person_id is rewritten through alias on UPDATE."""
    data = test_data

    # Update the email's person_id to the alias person
    await db_session.execute(
        text("""
            UPDATE contact_ops.emails
            SET person_id = :new_person_id
            WHERE id = :email_id
        """),
        {"new_person_id": data["person_1_id"], "email_id": data["email_id"]},
    )
    await db_session.commit()

    # The trigger should rewrite person_1_id → person_2_id
    result = await db_session.execute(
        text("""
            SELECT person_id FROM contact_ops.emails WHERE id = :email_id
        """),
        {"email_id": data["email_id"]},
    )
    row = result.fetchone()
    assert row[0] == data["person_2_id"]


@pytest.mark.asyncio
async def test_alias_rewrite_on_email_insert(db_session, test_data):
    """Verify email person_id is rewritten through alias on INSERT."""
    import uuid
    data = test_data

    new_email_id = uuid.uuid4()
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.emails (id, person_id, email_address, kind)
            VALUES (:id, :person_id, :email, 'work')
        """),
        {
            "id": new_email_id,
            "person_id": data["person_1_id"],
            "email": "REMOVED",
        },
    )
    await db_session.commit()

    result = await db_session.execute(
        text("""
            SELECT person_id FROM contact_ops.emails WHERE id = :id
        """),
        {"id": new_email_id},
    )
    row = result.fetchone()
    assert row[0] == data["person_2_id"]
