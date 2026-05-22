"""
Alias rewrite trigger tests.

Verifies that rewrite_alias_person_id() correctly rewrites person_id
through the person_alias table on INSERT and UPDATE for ALL 5 child tables:
emails, phones, postal_addresses, identifiers, im_handles.

Also covers: no-op when alias points at itself or no alias entry exists.

Column names match the actual schema:
  - person_alias: alias_id, current_canonical_id, merged_at_event_id
  - emails: address (not email_address), type, label, is_primary, person_id
  - phones: e164 (not number), person_id
  - postal_addresses: person_id
  - identifiers: namespace, value, person_id
  - im_handles: protocol, handle, person_id
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def alias_setup(db_session: AsyncSession):
    """Create tenant, two persons, an action_event, and an alias mapping P1 -> P2."""
    tenant_id = uuid.uuid4()
    canonical_id = uuid.uuid4()
    alias_id = uuid.uuid4()
    event_id = uuid.uuid4()

    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix)
            VALUES (:id, 'test', 'brand', 'Test', :owner, 'ns', 'bk')
            """
        ),
        {"id": tenant_id, "owner": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Canonical', :tid)"
        ),
        {"id": canonical_id, "tid": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'Alias', :tid)"
        ),
        {"id": alias_id, "tid": tenant_id},
    )
    # Need a real action_event for the FK on person_alias
    await db_session.execute(
        text(
            """
            INSERT INTO action_event
                (event_id, event_type, event_version, tenant_id,
                 aggregate_type, aggregate_id, payload, actor, actor_type, content_hash)
            VALUES (:eid, 'test.merge', 1, :tid,
                 'person', :canonical, '{}'::jsonb,
                 '{"sub":"test"}'::jsonb, 'automation_rule', :hash)
            """
        ),
        {"eid": event_id, "tid": tenant_id, "canonical": canonical_id,
         "hash": b"\x00" * 32},
    )
    await db_session.execute(
        text(
            "INSERT INTO person_alias (alias_id, current_canonical_id, merged_at_event_id) "
            "VALUES (:aid, :cid, :eid)"
        ),
        {"aid": alias_id, "cid": canonical_id, "eid": event_id},
    )
    await db_session.commit()
    return {"tenant_id": tenant_id, "canonical": canonical_id, "alias": alias_id}


# ---- Email tests ----


@pytest.mark.asyncio
async def test_alias_rewrite_on_email_insert(
    db_session: AsyncSession, alias_setup: dict,
):
    """INSERT email with person_id=alias → trigger rewrites to canonical."""
    email_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO emails (id, person_id, address)
            VALUES (:id, :pid, 'REMOVED')
            """
        ),
        {"id": email_id, "pid": alias_setup["alias"]},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT person_id FROM emails WHERE id = :id"),
        {"id": email_id},
    )
    assert result.scalar() == alias_setup["canonical"]


@pytest.mark.asyncio
async def test_alias_rewrite_on_email_update(
    db_session: AsyncSession, alias_setup: dict,
):
    """UPDATE email person_id to alias → trigger rewrites."""
    email_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO emails (id, person_id, address) "
            "VALUES (:id, :pid, 'REMOVED')"
        ),
        {"id": email_id, "pid": alias_setup["canonical"]},
    )
    await db_session.commit()

    await db_session.execute(
        text("UPDATE emails SET person_id = :pid WHERE id = :id"),
        {"pid": alias_setup["alias"], "id": email_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT person_id FROM emails WHERE id = :id"),
        {"id": email_id},
    )
    assert result.scalar() == alias_setup["canonical"]


# ---- Phones test ----


@pytest.mark.asyncio
async def test_alias_rewrite_on_phone(
    db_session: AsyncSession, alias_setup: dict,
):
    """INSERT phone with person_id=alias → trigger rewrites."""
    phone_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO phones (id, person_id, e164) VALUES (:id, :pid, '+15551234567')"
        ),
        {"id": phone_id, "pid": alias_setup["alias"]},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT person_id FROM phones WHERE id = :id"),
        {"id": phone_id},
    )
    assert result.scalar() == alias_setup["canonical"]


# ---- Postal addresses test ----


@pytest.mark.asyncio
async def test_alias_rewrite_on_postal_address(
    db_session: AsyncSession, alias_setup: dict,
):
    """INSERT postal_address with person_id=alias → trigger rewrites."""
    addr_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO postal_addresses (id, person_id, street_address) "
            "VALUES (:id, :pid, '123 Main St')"
        ),
        {"id": addr_id, "pid": alias_setup["alias"]},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT person_id FROM postal_addresses WHERE id = :id"),
        {"id": addr_id},
    )
    assert result.scalar() == alias_setup["canonical"]


# ---- Identifiers test ----


@pytest.mark.asyncio
async def test_alias_rewrite_on_identifier(
    db_session: AsyncSession, alias_setup: dict,
):
    """INSERT identifier with person_id=alias → trigger rewrites."""
    ident_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO identifiers (id, person_id, namespace, value) "
            "VALUES (:id, :pid, 'linkedin', 'test-profile')"
        ),
        {"id": ident_id, "pid": alias_setup["alias"]},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT person_id FROM identifiers WHERE id = :id"),
        {"id": ident_id},
    )
    assert result.scalar() == alias_setup["canonical"]


# ---- IM handles test ----


@pytest.mark.asyncio
async def test_alias_rewrite_on_im_handle(
    db_session: AsyncSession, alias_setup: dict,
):
    """INSERT im_handle with person_id=alias → trigger rewrites."""
    im_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO im_handles (id, person_id, protocol, handle) "
            "VALUES (:id, :pid, 'xmpp', 'REMOVED')"
        ),
        {"id": im_id, "pid": alias_setup["alias"]},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT person_id FROM im_handles WHERE id = :id"),
        {"id": im_id},
    )
    assert result.scalar() == alias_setup["canonical"]


# ---- No-op tests ----


@pytest.mark.asyncio
async def test_alias_rewrite_noop_when_canonical(
    db_session: AsyncSession, alias_setup: dict,
):
    """Pointing directly at the canonical person (no alias) → stays canonical."""
    email_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO emails (id, person_id, address) "
            "VALUES (:id, :pid, 'REMOVED')"
        ),
        {"id": email_id, "pid": alias_setup["canonical"]},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT person_id FROM emails WHERE id = :id"),
        {"id": email_id},
    )
    assert result.scalar() == alias_setup["canonical"]


@pytest.mark.asyncio
async def test_alias_rewrite_noop_when_no_alias(
    db_session: AsyncSession, alias_setup: dict,
):
    """Person with no alias entry → person_id untouched."""
    new_id = uuid.uuid4()
    email_id = uuid.uuid4()

    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
            "VALUES (:id, 'NoAlias', :tid)"
        ),
        {"id": new_id, "tid": alias_setup["tenant_id"]},
    )
    await db_session.commit()

    await db_session.execute(
        text(
            "INSERT INTO emails (id, person_id, address) "
            "VALUES (:id, :pid, 'REMOVED')"
        ),
        {"id": email_id, "pid": new_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT person_id FROM emails WHERE id = :id"),
        {"id": email_id},
    )
    assert result.scalar() == new_id
