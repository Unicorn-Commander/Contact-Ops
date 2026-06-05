# ruff: noqa: E402
"""Core upsert-extension tests for the ingest seam (P-00059).

Covers the ADDITIVE changes to `contact_ops/mcp/tools/people.py`:
  * provenance (source_label / source_kind / external_ref / consent_basis /
    lead_score / stage) is persisted on create AND on update;
  * the external_ref dedup FALLBACK matches when email/phone do NOT, scoped by
    (tenant, source_label, external_ref);
  * email/phone stays the PRIMARY match (the fallback never overrides it);
  * pre-existing upsert behavior is byte-for-byte unchanged.

Uses the same real-Postgres `db_session` + MCPContext pattern as
`test_mcp_tools_people.py`.
"""
from __future__ import annotations

import uuid

import pytest
import testcontainers.core.utils
from sqlalchemy import select, text

testcontainers.core.utils.raise_for_deprecated_parameter = (
    lambda *_args, **_kwargs: None
)

from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.people import (
    CreatePersonInput,
    IdentifierInput,
    UpsertPersonInput,
    create_person,
    upsert_person,
)
from contact_ops.models import Person

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
async def _tenant(db_session):
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix)
            VALUES (:id, 'ingest-core-test', 'personal', 'Ingest Core Test', :id,
                'ingest-core-test', 'ingest-core-test')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": TENANT_ID},
    )
    await db_session.flush()


def ctx(
    db_session,
    role: str = "STAFF",
    scopes: str = "person:read person:write person:bulk",
    tenant_id: uuid.UUID = TENANT_ID,
) -> MCPContext:
    return MCPContext(
        tenant_id=tenant_id,
        user_id=str(tenant_id),
        actor_chain={"sub": str(tenant_id)},
        human_authority=str(tenant_id),
        db=db_session,
        audit_db=db_session,
        request_id="ingest-core-test",
        claims={
            "sub": str(tenant_id),
            "realm_access": {"roles": [role]},
            "scope": scopes,
        },
    )


async def _load(db_session, person_id: uuid.UUID) -> Person:
    res = await db_session.execute(select(Person).where(Person.id == person_id))
    return res.scalar_one()


# ---------------------------------------------------------------------------
# Provenance persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provenance_persisted_on_create(db_session):
    c = ctx(db_session)
    out = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            display_name="Prov Person",
            emails=[{"address": "REMOVED"}],
            source_label="NorthwindLeads",
            source_kind="leadgen_app",
            external_ref="LV-100",
            consent_basis="opted_in",
            lead_score=77.5,
            stage="qualified",
        ),
    )
    assert out.action == "created"
    person = await _load(db_session, out.person_id)
    assert person.source_label == "NorthwindLeads"
    assert person.source_kind == "leadgen_app"
    assert person.external_ref == "LV-100"
    assert person.consent_basis == "opted_in"
    assert float(person.lead_score) == 77.5
    assert person.stage == "qualified"


@pytest.mark.asyncio
async def test_provenance_updated_on_existing_match_by_email(db_session):
    c = ctx(db_session)
    first = await create_person(
        c,
        CreatePersonInput(
            display_name="Existing", emails=[{"address": "REMOVED"}]
        ),
    )
    out = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            source_label="UnicornOutreach",
            consent_basis="legitimate_interest",
            lead_score=12.0,
            stage="contacted",
        ),
    )
    assert out.action == "updated"
    assert out.person_id == first.person_id
    # provenance columns are among the changed fields
    assert "source_label" in out.changed_fields
    assert "consent_basis" in out.changed_fields
    person = await _load(db_session, first.person_id)
    assert person.source_label == "UnicornOutreach"
    assert person.consent_basis == "legitimate_interest"
    assert person.stage == "contacted"


@pytest.mark.asyncio
async def test_absent_provenance_does_not_clobber(db_session):
    """A later upsert that omits a field must not NULL out a value an earlier
    push recorded."""
    c = ctx(db_session)
    out1 = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            display_name="Keep",
            emails=[{"address": "REMOVED"}],
            source_label="NorthwindLeads",
            consent_basis="opted_in",
            stage="qualified",
        ),
    )
    # Second push: only updates the headline, omits all provenance.
    await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            headline="New headline",
        ),
    )
    person = await _load(db_session, out1.person_id)
    assert person.source_label == "NorthwindLeads"  # preserved
    assert person.consent_basis == "opted_in"  # preserved
    assert person.stage == "qualified"  # preserved
    assert person.headline == "New headline"


# ---------------------------------------------------------------------------
# external_ref fallback match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_ref_fallback_matches_when_no_email(db_session):
    """A lead WITHOUT email/phone is recognized on a repeat push via
    (tenant, source_label, external_ref)."""
    c = ctx(db_session)
    out1 = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="external_ref", value="REF-1")],
            display_name="NoEmail Lead",
            source_label="LoopNetLeads",
            external_ref="REF-1",
            consent_basis="public_record",
        ),
    )
    assert out1.action == "created"

    # Second push, same source_label + external_ref, still no email → UPDATE, not a
    # duplicate.
    out2 = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="external_ref", value="REF-1")],
            display_name="NoEmail Lead Renamed",
            source_label="LoopNetLeads",
            external_ref="REF-1",
            consent_basis="public_record",
            conflict_strategy="prefer_incoming",
        ),
    )
    assert out2.person_id == out1.person_id
    assert out2.action == "updated"

    # Exactly one person exists for that external_ref in this tenant.
    res = await db_session.execute(
        select(Person).where(
            Person.canonical_owner_tenant_id == TENANT_ID,
            Person.source_label == "LoopNetLeads",
            Person.external_ref == "REF-1",
        )
    )
    assert len(res.scalars().all()) == 1


@pytest.mark.asyncio
async def test_external_ref_fallback_scoped_by_source_label(db_session):
    """The same external_ref string under a DIFFERENT source_label is a different
    person (the fallback is scoped to the producer)."""
    c = ctx(db_session)
    a = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="external_ref", value="SHARED")],
            display_name="From App A",
            source_label="AppA",
            external_ref="SHARED",
            consent_basis="opted_in",
        ),
    )
    b = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="external_ref", value="SHARED")],
            display_name="From App B",
            source_label="AppB",
            external_ref="SHARED",
            consent_basis="opted_in",
        ),
    )
    assert a.action == "created"
    assert b.action == "created"
    assert a.person_id != b.person_id


@pytest.mark.asyncio
async def test_email_match_is_primary_over_external_ref(db_session):
    """When email matches, the external_ref fallback is NOT consulted — the email
    match wins even if a different external_ref is supplied."""
    c = ctx(db_session)
    first = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            display_name="Primary",
            emails=[{"address": "REMOVED"}],
            source_label="AppA",
            external_ref="EXT-A",
            consent_basis="opted_in",
        ),
    )
    # Same email, but a DIFFERENT external_ref + an external_ref identifier present.
    out = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[
                IdentifierInput(namespace="email", value="REMOVED"),
                IdentifierInput(namespace="external_ref", value="EXT-DIFFERENT"),
            ],
            display_name="Primary Updated",
            source_label="AppA",
            external_ref="EXT-DIFFERENT",
            consent_basis="opted_in",
            conflict_strategy="prefer_incoming",
        ),
    )
    assert out.person_id == first.person_id
    assert out.action == "updated"


# ---------------------------------------------------------------------------
# Existing behavior unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_upsert_behavior_unchanged(db_session):
    """An upsert with NO provenance fields behaves exactly as before: create on
    first call, update-by-email on the second, identity fields applied."""
    c = ctx(db_session)
    created = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            display_name="Plain",
            emails=[{"address": "REMOVED"}],
        ),
    )
    assert created.action == "created"
    person = await _load(db_session, created.person_id)
    # No provenance was supplied → columns stay NULL (no accidental writes).
    assert person.source_label is None
    assert person.consent_basis is None
    assert person.external_ref is None

    updated = await upsert_person(
        c,
        UpsertPersonInput(
            match_by=[IdentifierInput(namespace="email", value="REMOVED")],
            display_name="Plain Renamed",
            conflict_strategy="prefer_incoming",
        ),
    )
    assert updated.action == "updated"
    assert updated.person_id == created.person_id
    assert updated.changed_fields == ["display_name"]
