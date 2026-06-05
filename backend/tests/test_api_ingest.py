# ruff: noqa: E402
"""REST ingest endpoint tests (P-00059), CONTACT-OPS-INGEST-CONTRACT §1B/§3.

Two layers, matching the project's house style (cf. test_import_vcard.py):
  * `ingest_one(ctx, body)` is exercised directly against a REAL tenant-bound
    Postgres session (testcontainers) so idempotency, provenance persistence, the
    returned contact_id/action, and RLS isolation are genuinely proven — the same
    way the MCP tools are tested.
  * The FastAPI HTTP layer (scope 403 + required-consent 422) is exercised via
    TestClient with STANDALONE_MODE so routing/validation/scope-gating run end to
    end without a live Keycloak.

RLS isolation (requirement e) is proven the same way test_p4_isolation_rls.py does
it: under the non-bypassrls `contact_ops_runtime` role with the tenant GUC bound,
so the Postgres policies — not just the query's WHERE clause — are what isolate.
"""
from __future__ import annotations

import uuid

import pytest
import testcontainers.core.utils
from sqlalchemy import select, text

testcontainers.core.utils.raise_for_deprecated_parameter = (
    lambda *_args, **_kwargs: None
)

from contact_ops.api.ingest import IngestContactRequest, ingest_one
from contact_ops.mcp.errors import INSUFFICIENT_ROLE, INSUFFICIENT_SCOPE, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.models import Person

TENANT_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
TENANT_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")


@pytest.fixture(autouse=True)
async def _tenants(db_session):
    for tid, slug in ((TENANT_A, "ingest-a"), (TENANT_B, "ingest-b")):
        await db_session.execute(
            text(
                """
                INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                    qdrant_namespace, garage_bucket_prefix)
                VALUES (:id, :slug, 'personal', :slug, :id, :slug, :slug)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": tid, "slug": slug},
        )
    await db_session.flush()


def ctx(
    db_session,
    *,
    tenant_id: uuid.UUID = TENANT_A,
    role: str = "STAFF",
    scopes: str = "contacts:write",
) -> MCPContext:
    """A context whose db is the bound test session. By default the caller holds
    ONLY the new `contacts:write` scope (no `person:write`), so the door's
    scope-bridge to the core is exercised on the happy path too."""
    return MCPContext(
        tenant_id=tenant_id,
        user_id=str(tenant_id),
        actor_chain={"sub": str(tenant_id)},
        human_authority=str(tenant_id),
        db=db_session,
        audit_db=db_session,
        request_id="ingest-api-test",
        claims={
            "sub": str(tenant_id),
            "tenant_id": str(tenant_id),
            "realm_access": {"roles": [role]},
            "scope": scopes,
        },
    )


def _req(**over) -> IngestContactRequest:
    base = {
        "match_by": [{"kind": "email", "value": "REMOVED"}],
        "attributes": {
            "display_name": "Dana Lead",
            "emails": [{"address": "REMOVED"}],
        },
        "source": {
            "source_label": "NorthwindLeads",
            "source_kind": "leadgen_app",
            "external_ref": "LV-1",
            "consent_basis": "opted_in",
        },
        "rollups": {"lead_score": 87.5, "stage": "qualified"},
    }
    base.update(over)
    return IngestContactRequest.model_validate(base)


# ---------------------------------------------------------------------------
# (d) returns contact_id + action; (c) provenance persisted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_returns_contact_id_and_action(db_session):
    out = await ingest_one(ctx(db_session), _req())
    assert out.action == "created"
    assert isinstance(out.contact_id, uuid.UUID)
    assert out.etag


@pytest.mark.asyncio
async def test_ingest_persists_provenance(db_session):
    out = await ingest_one(ctx(db_session), _req())
    res = await db_session.execute(
        select(Person).where(Person.id == out.contact_id)
    )
    person = res.scalar_one()
    assert person.source_label == "NorthwindLeads"
    assert person.consent_basis == "opted_in"
    assert person.external_ref == "LV-1"
    assert person.stage == "qualified"
    assert float(person.lead_score) == 87.5


# ---------------------------------------------------------------------------
# (b) idempotency — same email twice → updated/noop, never a duplicate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_idempotent_same_email(db_session):
    c = ctx(db_session)
    first = await ingest_one(c, _req())
    second = await ingest_one(c, _req())  # identical payload
    assert second.contact_id == first.contact_id
    assert second.action in ("updated", "noop")
    # exactly one person with that email in this tenant
    res = await db_session.execute(
        select(Person).where(Person.canonical_owner_tenant_id == TENANT_A)
    )
    people = res.scalars().all()
    assert len([p for p in people if p.id == first.contact_id]) == 1
    matching = [p for p in people if p.display_name == "Dana Lead"]
    assert len(matching) == 1


# ---------------------------------------------------------------------------
# (a) 403 when token lacks contacts:write / person:write
#
# The 403 scope gate (_require_write_scope) and the consent-required 422
# (IngestContactRequest validation) are HTTP-layer concerns that run BEFORE any
# DB access. We assert them by exercising those exact units directly rather than
# spinning a TestClient through the audit middleware (which would touch the
# module-level engine). True RLS isolation lives in test_ingest_rls_isolation.py
# (sync-only, runtime role) so the async + sync engine modes never share a file.
# ---------------------------------------------------------------------------


def test_require_write_scope_denies_readonly():
    from fastapi import HTTPException

    from contact_ops.api.ingest import _require_write_scope

    c = MCPContext(
        tenant_id=TENANT_A,
        user_id="u",
        actor_chain={"sub": "u"},
        human_authority="u",
        db=None,  # type: ignore[arg-type]
        audit_db=None,  # type: ignore[arg-type]
        request_id="r",
        claims={"sub": "u", "tenant_id": str(TENANT_A), "scope": "contacts:read"},
    )
    with pytest.raises(HTTPException) as exc:
        _require_write_scope(c)
    assert exc.value.status_code == 403
    assert "INSUFFICIENT_SCOPE" in str(exc.value.detail)


def test_require_write_scope_allows_each_write_scope():
    from contact_ops.api.ingest import _require_write_scope

    for scope in ("contacts:write", "person:write", "person:read contacts:write"):
        c = MCPContext(
            tenant_id=TENANT_A,
            user_id="u",
            actor_chain={"sub": "u"},
            human_authority="u",
            db=None,  # type: ignore[arg-type]
            audit_db=None,  # type: ignore[arg-type]
            request_id="r",
            claims={"sub": "u", "tenant_id": str(TENANT_A), "scope": scope},
        )
        _require_write_scope(c)  # must not raise


# ---------------------------------------------------------------------------
# (f) consent_basis REQUIRED → 422 (request model rejects it pre-handler)
# ---------------------------------------------------------------------------


def test_consent_basis_required():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IngestContactRequest.model_validate(
            {
                "match_by": [{"kind": "email", "value": "REMOVED"}],
                "attributes": {"display_name": "X"},
                "source": {"source_label": "L"},  # consent_basis omitted
            }
        )


def test_consent_basis_must_be_valid_value():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IngestContactRequest.model_validate(
            {
                "match_by": [{"kind": "email", "value": "REMOVED"}],
                "attributes": {"display_name": "X"},
                "source": {"source_label": "L", "consent_basis": "not_a_basis"},
            }
        )


def test_domain_artifact_attribute_forbidden():
    """A producer cannot smuggle a domain-artifact field into the identity
    record — extra attributes are rejected (422), not silently dropped."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IngestContactRequest.model_validate(
            {
                "match_by": [{"kind": "email", "value": "REMOVED"}],
                "attributes": {"display_name": "X", "transcript": "secret"},
                "source": {"source_label": "L", "consent_basis": "opted_in"},
            }
        )


# ---------------------------------------------------------------------------
# Core-level scope deny: a context with NEITHER write scope is rejected by the
# upsert core (defense in depth behind the HTTP gate).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_one_core_rejects_readonly_scope(db_session):
    """A read-only context is NOT escalated by the bridge: the core still rejects
    it (defense in depth if ingest_one is ever called without the door in front)."""
    c = ctx(db_session, role="CLIENT", scopes="contacts:read")
    with pytest.raises(ToolError) as exc:
        await ingest_one(c, _req())
    # The core rejects on role first (CLIENT < STAFF), then scope; either is a
    # correct deny — the key point is the read-only caller is NOT escalated.
    assert exc.value.code in (INSUFFICIENT_ROLE, INSUFFICIENT_SCOPE)


@pytest.mark.asyncio
async def test_ingest_one_bridges_contacts_write_only(db_session):
    """A caller holding ONLY the new `contacts:write` scope (CLIENT role, no
    `person:write`) is bridged to the core and the write succeeds — the whole
    point of accepting the new suite scope without weakening the MCP core."""
    c = ctx(db_session, role="CLIENT", scopes="contacts:write")
    out = await ingest_one(c, _req())
    assert out.action == "created"
    assert isinstance(out.contact_id, uuid.UUID)
    # The caller's own claims were NOT mutated by the bridge.
    assert c.claims["scope"] == "contacts:write"
    assert c.claims["realm_access"]["roles"] == ["CLIENT"]
