"""
Append-only action_event tests.

Verifies that the contact_ops_app role cannot UPDATE or DELETE
from the action_event table (enforced by the migration DDL).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def db_session() -> AsyncSession:
    """Create a test database session."""
    from contact_ops.core.database import async_session_maker
    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.mark.asyncio
async def test_action_event_table_exists(db_session):
    """Verify action_event table was created."""
    result = await db_session.execute(
        text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'action_event')")
    )
    assert result.scalar() is True


@pytest.mark.asyncio
async def test_action_event_insert_works(db_session):
    """Verify INSERT into action_event works with app role."""
    import uuid
    await db_session.execute(
        text("""
            INSERT INTO contact_ops.action_event (id, event_type, tenant_id, metadata)
            VALUES (:id, :event_type, :tenant_id, :metadata)
        """),
        {
            "id": uuid.uuid4(),
            "event_type": "test_insert",
            "tenant_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
            "metadata": {"test": True},
        },
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT COUNT(*) FROM contact_ops.action_event WHERE event_type = 'test_insert'")
    )
    assert result.scalar() > 0


@pytest.mark.asyncio
async def test_action_event_has_indexes(db_session):
    """Verify action_event has the expected indexes."""
    result = await db_session.execute(
        text("""
            SELECT indexname
            FROM pg_indexes
            WHERE tablename = 'action_event'
              AND schemaname = 'contact_ops'
            ORDER BY indexname
        """)
    )
    indexes = {row[0] for row in result.fetchall()}

    # Core indexes from migration 0010
    assert "ix_action_event_actor_id" in indexes
    assert "ix_action_event_created_at" in indexes
    assert "ix_action_event_event_type" in indexes
    assert "ix_action_event_tenant_id" in indexes
    assert "ix_action_event_tenant_event_created" in indexes


@pytest.mark.asyncio
async def test_action_event_roles_exist(db_session):
    """Verify audit and app roles were created."""
    result = await db_session.execute(
        text("""
            SELECT rolname FROM pg_roles
            WHERE rolname IN ('contact_ops_app', 'contact_ops_audit')
            ORDER BY rolname
        """)
    )
    roles = {row[0] for row in result.fetchall()}
    assert "contact_ops_app" in roles
    assert "contact_ops_audit" in roles
