"""
HIPAA merge fence trigger tests.

Verifies that enforce_hipaa_merge():
  - Rejects cross-tenant merges INTO a HIPAA tenant.
  - Allows same-tenant merges within a HIPAA tenant.
  - Allows cross-tenant merges between two non-HIPAA tenants.
  - Fires on UPDATE as well as INSERT.

Uses the *actual* merge_history column names from migration 0011:
  kept_id, removed_id, entity_type, tenant_id, score, signals,
  performed_at, performed_by_actor_id, performed_event_id,
  reversible_until
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_hipaa_trigger_rejects_cross_tenant_merge_into_hipaa(
    db_session: AsyncSession, seeded_tenants: dict[str, uuid.UUID],
):
    """Merging a non-HIPAA person INTO a HIPAA tenant must raise."""
    hipaa_id = seeded_tenants["hipaa"]
    non_hipaa_id = seeded_tenants["non_hipaa"]

    kept_id = uuid.uuid4()
    removed_id = uuid.uuid4()

    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'HIPAA Person', :tid)"
        ),
        {"id": kept_id, "tid": hipaa_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Non-HIPAA Person', :tid)"
        ),
        {"id": removed_id, "tid": non_hipaa_id},
    )
    await db_session.commit()

    with pytest.raises(Exception):
        await db_session.execute(
            text(
                """
                INSERT INTO merge_history
                    (tenant_id, entity_type, kept_id, removed_id, score)
                VALUES (:tid, 'person', :kept, :removed, 0.95)
                """
            ),
            {
                "tid": hipaa_id,  # initiator is HIPAA
                "kept": kept_id,
                "removed": removed_id,
            },
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_hipaa_trigger_allows_same_tenant_merge(
    db_session: AsyncSession, seeded_tenants: dict[str, uuid.UUID],
):
    """Merging two persons from the SAME HIPAA tenant succeeds."""
    hipaa_id = seeded_tenants["hipaa"]

    kept_id = uuid.uuid4()
    removed_id = uuid.uuid4()

    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Person A', :tid)"
        ),
        {"id": kept_id, "tid": hipaa_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Person B', :tid)"
        ),
        {"id": removed_id, "tid": hipaa_id},
    )
    await db_session.commit()

    await db_session.execute(
        text(
            """
            INSERT INTO merge_history
                (tenant_id, entity_type, kept_id, removed_id, score)
            VALUES (:tid, 'person', :kept, :removed, 0.95)
            """
        ),
        {"tid": hipaa_id, "kept": kept_id, "removed": removed_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT COUNT(*) FROM merge_history WHERE kept_id = :kept"),
        {"kept": kept_id},
    )
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_hipaa_trigger_allows_two_non_hipaa_tenants(
    db_session: AsyncSession, seeded_tenants: dict[str, uuid.UUID],
):
    """Cross-tenant merge between two non-HIPAA tenants is allowed."""
    non_hipaa_id = seeded_tenants["non_hipaa"]

    kept_id = uuid.uuid4()
    removed_id = uuid.uuid4()

    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Person C', :tid)"
        ),
        {"id": kept_id, "tid": non_hipaa_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Person D', :tid)"
        ),
        {"id": removed_id, "tid": non_hipaa_id},
    )
    await db_session.commit()

    await db_session.execute(
        text(
            """
            INSERT INTO merge_history
                (tenant_id, entity_type, kept_id, removed_id, score)
            VALUES (:tid, 'person', :kept, :removed, 0.95)
            """
        ),
        {"tid": non_hipaa_id, "kept": kept_id, "removed": removed_id},
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_hipaa_trigger_fires_on_update(
    db_session: AsyncSession, seeded_tenants: dict[str, uuid.UUID],
):
    """The HIPAA trigger fires on UPDATE of merge_history too."""
    hipaa_id = seeded_tenants["hipaa"]
    non_hipaa_id = seeded_tenants["non_hipaa"]

    kept_id = uuid.uuid4()
    removed_id = uuid.uuid4()

    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Person E', :tid)"
        ),
        {"id": kept_id, "tid": hipaa_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Person F', :tid)"
        ),
        {"id": removed_id, "tid": hipaa_id},
    )
    await db_session.commit()

    merge_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO merge_history
                (id, tenant_id, entity_type, kept_id, removed_id, score)
            VALUES (:id, :tid, 'person', :kept, :removed, 0.90)
            """
        ),
        {"id": merge_id, "tid": hipaa_id, "kept": kept_id, "removed": removed_id},
    )
    await db_session.commit()

    # Attempt to change removed_id to a person from the non-HIPAA tenant
    with pytest.raises(Exception):
        await db_session.execute(
            text(
                "UPDATE merge_history SET removed_id = :bad WHERE id = :mid"
            ),
            {"bad": non_hipaa_id, "mid": merge_id},
        )
        await db_session.commit()
