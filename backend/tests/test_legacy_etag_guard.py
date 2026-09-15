"""Non-DB tests for the legacy email/phone/address/employment optimistic-concurrency etag.

The four older update tools used to raise ``VALIDATION_FAILED`` on an etag
mismatch, which the REST surface maps to 400. The newer im_handle/url/identifier
tools raise ``STALE_ETAG`` instead, which ``api/people.py`` maps to 409 —
matching ``update_person``. These tests drive the four migrated guards with a fake
DB (no Postgres) and assert:

  * a stale etag is rejected with STALE_ETAG before any mutation;
  * genuine validation errors (row not found) still raise VALIDATION_FAILED.

The DB-backed paths (real touch_updated trigger bumping updated_at on UPDATE) are
covered by the migration roundtrip tests in the testcontainer suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from contact_ops.mcp.errors import STALE_ETAG, VALIDATION_FAILED, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.addresses import UpdateAddressInput, update_address
from contact_ops.mcp.tools.emails import UpdateEmailInput, update_email
from contact_ops.mcp.tools.employment import UpdateEmploymentInput, update_employment
from contact_ops.mcp.tools.phones import UpdatePhoneInput, update_phone
from contact_ops.models import Email, Person, PersonOrgRole, Phone, PostalAddress

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _FakeDb:
    """Answers ``get(Model, id)`` from a per-model row map; everything else no-ops."""

    def __init__(self, rows: dict[type, Any]) -> None:
        self._rows = rows

    async def get(self, model: type, _ident: uuid.UUID) -> Any:
        return self._rows.get(model)

    async def flush(self) -> None:
        return None

    async def scalar(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def execute(self, *_args: object, **_kwargs: object) -> Any:
        class _Scalars:
            def scalars(self) -> Any:
                return self

            def all(self) -> list[Any]:
                return []

        return _Scalars()


def _person() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        canonical_owner_tenant_id=TENANT,
        merge_status=None,
        display_name="Test",
    )


def _ctx(db: _FakeDb, scopes: str) -> MCPContext:
    return MCPContext(
        tenant_id=TENANT,
        user_id="test-user",
        actor_chain={"sub": "test-user"},
        human_authority=str(TENANT),
        db=db,  # type: ignore[arg-type]
        audit_db=db,  # type: ignore[arg-type]
        request_id="test-request",
        claims={"realm_access": {"roles": ["STAFF"]}, "scope": scopes},
    )


def _email(updated_at: datetime = NOW) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        updated_at=updated_at,
        address="REMOVED",
        type=SimpleNamespace(value="work"),
        label=None,
        is_primary=True,
        is_verified=False,
        verified_at=None,
        deliverability_status=SimpleNamespace(value="unknown"),
        opted_out=False,
        valid_until=None,
    )


def _phone(updated_at: datetime = NOW) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        updated_at=updated_at,
        e164="+15551234567",
        type=SimpleNamespace(value="mobile"),
        label=None,
        is_primary=True,
        is_verified=False,
        verified_at=None,
        carrier=None,
    )


def _address(updated_at: datetime = NOW) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        organization_id=None,
        updated_at=updated_at,
        is_primary=True,
    )


def _employment(updated_at: datetime = NOW) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        updated_at=updated_at,
        is_primary=True,
    )


# ── stale etag rejected with STALE_ETAG (not VALIDATION_FAILED) ─────────────


@pytest.mark.asyncio
async def test_update_email_rejects_stale_etag_as_stale_etag() -> None:
    row = _email()
    ctx = _ctx(_FakeDb({Email: row, Person: _person()}), scopes="email:write")
    req = UpdateEmailInput(email_id=row.id, etag="stale")

    with pytest.raises(ToolError) as exc:
        await update_email(ctx, req)
    assert exc.value.code == STALE_ETAG


@pytest.mark.asyncio
async def test_update_phone_rejects_stale_etag_as_stale_etag() -> None:
    row = _phone()
    ctx = _ctx(_FakeDb({Phone: row, Person: _person()}), scopes="phone:write")
    req = UpdatePhoneInput(phone_id=row.id, etag="stale")

    with pytest.raises(ToolError) as exc:
        await update_phone(ctx, req)
    assert exc.value.code == STALE_ETAG


@pytest.mark.asyncio
async def test_update_address_rejects_stale_etag_as_stale_etag() -> None:
    row = _address()
    ctx = _ctx(_FakeDb({PostalAddress: row, Person: _person()}), scopes="address:write")
    req = UpdateAddressInput(address_id=row.id, etag="stale")

    with pytest.raises(ToolError) as exc:
        await update_address(ctx, req)
    assert exc.value.code == STALE_ETAG


@pytest.mark.asyncio
async def test_update_employment_rejects_stale_etag_as_stale_etag() -> None:
    row = _employment()
    ctx = _ctx(_FakeDb({PersonOrgRole: row, Person: _person()}), scopes="org:write person:write")
    req = UpdateEmploymentInput(employment_id=row.id, etag="stale")

    with pytest.raises(ToolError) as exc:
        await update_employment(ctx, req)
    assert exc.value.code == STALE_ETAG


# ── genuine validation errors are NOT reclassified ──────────────────────────


@pytest.mark.asyncio
async def test_update_email_missing_row_is_validation_failed() -> None:
    ctx = _ctx(_FakeDb({}), scopes="email:write")
    req = UpdateEmailInput(email_id=uuid.uuid4(), etag="anything")

    with pytest.raises(ToolError) as exc:
        await update_email(ctx, req)
    assert exc.value.code == VALIDATION_FAILED


@pytest.mark.asyncio
async def test_update_phone_missing_row_is_validation_failed() -> None:
    ctx = _ctx(_FakeDb({}), scopes="phone:write")
    req = UpdatePhoneInput(phone_id=uuid.uuid4(), etag="anything")

    with pytest.raises(ToolError) as exc:
        await update_phone(ctx, req)
    assert exc.value.code == VALIDATION_FAILED


@pytest.mark.asyncio
async def test_update_address_missing_row_is_validation_failed() -> None:
    ctx = _ctx(_FakeDb({}), scopes="address:write")
    req = UpdateAddressInput(address_id=uuid.uuid4(), etag="anything")

    with pytest.raises(ToolError) as exc:
        await update_address(ctx, req)
    assert exc.value.code == VALIDATION_FAILED


@pytest.mark.asyncio
async def test_update_employment_missing_row_is_validation_failed() -> None:
    ctx = _ctx(_FakeDb({}), scopes="org:write person:write")
    req = UpdateEmploymentInput(employment_id=uuid.uuid4(), etag="anything")

    with pytest.raises(ToolError) as exc:
        await update_employment(ctx, req)
    assert exc.value.code == VALIDATION_FAILED
