"""
person_names RLS tests.

Verifies the person_names table is fenced to the person's owning tenant with
the same parent-visibility RLS form as the other person-child tables (0036):
a person_name row is visible only under the tenant that owns its person, and
inserts/modifies are owner-scoped.

NOTE: these require the testcontainers Postgres (Docker) that the suite spins up
in conftest; they do NOT run in a Docker-less environment.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError


@pytest.fixture
async def person_name_setup(db_session: AsyncSession):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    person_a = uuid.uuid4()
    person_b = uuid.uuid4()

    for tid in (tenant_a, tenant_b):
        await db_session.execute(
            text(
                "INSERT INTO tenants (id, slug, kind, display_name, owner_user_id, "
                "qdrant_namespace, garage_bucket_prefix) "
                "VALUES (:id, :slug, 'brand', :slug, :owner, 'ns', 'bk')"
            ),
            {"id": tid, "slug": str(tid)[:8], "owner": tid},
        )
    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Person A', :tid)"
        ),
        {"id": person_a, "tid": tenant_a},
    )
    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Person B', :tid)"
        ),
        {"id": person_b, "tid": tenant_b},
    )
    await db_session.commit()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "person_a": person_a,
        "person_b": person_b,
    }


@pytest.mark.asyncio
async def test_person_name_visible_only_to_owning_tenant(
    db_engine, person_name_setup: dict,
):
    s = person_name_setup
    name_id = uuid.uuid4()

    async with db_engine.connect() as conn:
        await conn.execute(text("SET LOCAL ROLE contact_ops_app"))
        assert await conn.scalar(text("SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"))
        # Insert the person_name under tenant A's GUC (owner).
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": str(s["tenant_a"])},
        )
        await conn.execute(
            text(
                "INSERT INTO person_names (id, person_id, name_type, value) "
                "VALUES (:id, :pid, 'nickname', 'Diesel')"
            ),
            {"id": name_id, "pid": s["person_a"]},
        )

        # Under tenant A: visible.
        rows_a = (
            await conn.execute(
                text("SELECT id FROM person_names WHERE id = :id"),
                {"id": name_id},
            )
        ).fetchall()
        assert len(rows_a) == 1

        # Under tenant B: RLS hides it.
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": str(s["tenant_b"])},
        )
        rows_b = (
            await conn.execute(
                text("SELECT id FROM person_names WHERE id = :id"),
                {"id": name_id},
            )
        ).fetchall()
        assert len(rows_b) == 0

        # The foreign tenant cannot insert a child of the hidden parent either.
        with pytest.raises(DBAPIError):
            async with conn.begin_nested():
                await conn.execute(
                    text("INSERT INTO person_names (person_id, name_type, value) "
                         "VALUES (:pid, 'aka', 'Foreign write')"),
                    {"pid": s["person_a"]},
                )


@pytest.mark.asyncio
async def test_person_name_backfill_from_nicknames(db_engine):
    """The 0050 backfill seeds a nickname person_name from persons.nicknames."""
    tid = uuid.uuid4()
    person = uuid.uuid4()

    async with db_engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, slug, kind, display_name, owner_user_id, "
                "qdrant_namespace, garage_bucket_prefix) "
                "VALUES (:id, 'backfill', 'brand', 'Backfill', :owner, 'ns', 'bk')"
            ),
            {"id": tid, "owner": tid},
        )
        await conn.execute(
            text(
                "INSERT INTO persons (id, display_name, canonical_owner_tenant_id, nicknames) "
                "VALUES (:id, 'Diesel Dude', :tid, ARRAY['Diesel'])"
            ),
            {"id": person, "tid": tid},
        )
        # Re-run the backfill INSERT from 0050 (idempotent).
        await conn.execute(
            text(
                "INSERT INTO person_names (person_id, name_type, value, is_primary) "
                "SELECT p.id, 'nickname', n, false "
                "FROM persons p "
                "CROSS JOIN LATERAL unnest(p.nicknames) AS n "
                "WHERE p.id = :pid "
                "  AND p.nicknames IS NOT NULL "
                "  AND cardinality(p.nicknames) > 0 "
                "ON CONFLICT DO NOTHING"
            ),
            {"pid": person},
        )
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
        rows = (
            await conn.execute(
                text(
                    "SELECT value FROM person_names "
                    "WHERE person_id = :pid AND name_type = 'nickname'"
                ),
                {"pid": person},
            )
        ).fetchall()
        assert rows and rows[0][0] == "Diesel"
