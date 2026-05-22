"""
HIPAA merge fence trigger tests.

Verifies that the enforce_hipaa_merge() trigger prevents cross-tenant
merges when either tenant has hipaa_mode = true.
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
async def hipaa_tenants(db_session):
    """Create two tenants, one with HIPAA mode enabled."""
    hipaa_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    non_hipaa_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

    await db_session.execute(
        text("""
            INSERT INTO contact_ops.tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace)
            VALUES (:id, :slug, 'personal', :name, :owner, :hipaa, :ns)
        """),
        {"id": hipaa_id, "slug": "hipaa-tenant", "name": "HIPAA Tenant",
         "owner": hipaa_id, "hipaa": True, "ns": "hipaa-ns"},
    )
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace)
            VALUES (:id, :slug, 'personal', :name, :owner, :hipaa, :ns)
        """),
        {"id": non_hipaa_id, "slug": "non-hipaa-tenant", "name": "Non-HIPAA Tenant",
         "owner": non_hipaa_id, "hipaa": False, "ns": "non-hipaa-ns"},
    )
    await db_session.commit()
    return hipaa_id, non_hipaa_id


@pytest.mark.asyncio
async def test_hipaa_trigger_prevents_cross_tenant_merge(db_session, hipaa_tenants):
    """Verify HIPAA trigger blocks merge between HIPAA and non-HIPAA tenants."""
    hipaa_id, non_hipaa_id = hipaa_tenants

    person_1_id = uuid.uuid4()
    person_2_id = uuid.uuid4()
    merge_id = uuid.uuid4()

    # Create two persons in different tenants
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.persons (id, display_name, canonical_owner_tenant_id)
            VALUES (:id, :name, :tenant_id)
        """),
        {"id": person_1_id, "name": "Person 1", "tenant_id": hipaa_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.persons (id, display_name, canonical_owner_tenant_id)
            VALUES (:id, :name, :tenant_id)
        """),
        {"id": person_2_id, "name": "Person 2", "tenant_id": non_hipaa_id},
    )

    # Attempt merge — should be blocked by trigger
    with pytest.raises(Exception):
        await db_session.execute(
            text("""
                INSERT INTO contact_ops.merge_history
                    (id, source_person_id, target_person_id, initiator_tenant_id,
                     source_tenant_id, target_tenant_id, merge_status)
                VALUES (:id, :source, :target, :tenant, :source_tenant, :target_tenant, 'pending')
            """),
            {
                "id": merge_id,
                "source": person_1_id,
                "target": person_2_id,
                "tenant": hipaa_id,
                "source_tenant": hipaa_id,
                "target_tenant": non_hipaa_id,
            },
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_hipaa_trigger_allows_same_tenant_merge(db_session, hipaa_tenants):
    """Verify HIPAA trigger allows merge within the same HIPAA tenant."""
    hipaa_id, _ = hipaa_tenants

    person_1_id = uuid.uuid4()
    person_2_id = uuid.uuid4()
    merge_id = uuid.uuid4()

    await db_session.execute(
        text("""
            INSERT INTO contact_ops.persons (id, display_name, canonical_owner_tenant_id)
            VALUES (:id, :name, :tenant_id)
        """),
        {"id": person_1_id, "name": "Person 1", "tenant_id": hipaa_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.persons (id, display_name, canonical_owner_tenant_id)
            VALUES (:id, :name, :tenant_id)
        """),
        {"id": person_2_id, "name": "Person 2", "tenant_id": hipaa_id},
    )

    # Same-tenant merge should succeed
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.merge_history
                (id, source_person_id, target_person_id, initiator_tenant_id,
                 source_tenant_id, target_tenant_id, merge_status)
            VALUES (:id, :source, :target, :tenant, :source_tenant, :target_tenant, 'pending')
        """),
        {
            "id": merge_id,
            "source": person_1_id,
            "target": person_2_id,
            "tenant": hipaa_id,
            "source_tenant": hipaa_id,
            "target_tenant": hipaa_id,
        },
    )
    await db_session.commit()

    # Verify merge record exists
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM contact_ops.merge_history WHERE id = :id"),
        {"id": merge_id},
    )
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_current_tenant_id_function(db_session):
    """Verify current_tenant_id() function is callable."""
    result = await db_session.execute(text("SELECT current_tenant_id()"))
    # Returns NULL when no GUC is set — that's expected
    assert result.scalar() is None or isinstance(result.scalar(), uuid.UUID)


@pytest.mark.asyncio
async def test_tenant_is_hipaa_function(db_session, hipaa_tenants):
    """Verify tenant_is_hipaa() function returns correct values."""
    hipaa_id, non_hipaa_id = hipaa_tenants

    result = await db_session.execute(
        text("SELECT tenant_is_hipaa(:id)"),
        {"id": hipaa_id},
    )
    assert result.scalar() is True

    result = await db_session.execute(
        text("SELECT tenant_is_hipaa(:id)"),
        {"id": non_hipaa_id},
    )
    assert result.scalar() is False
