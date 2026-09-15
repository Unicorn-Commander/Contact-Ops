"""Non-DB tests for the post-write etag of the legacy contact detail updates.

The ``touch_updated`` trigger bumps ``updated_at`` at the DB level on UPDATE, so
an ORM row is one write behind right after ``flush()``. ``update_email``,
``update_phone``, ``update_address`` and ``update_employment`` used to serialize
``_etag(row)`` without reloading, returning the PRE-write etag — a client that
reused it immediately got a false ``STALE_ETAG``. The fix mirrors
``im_handles.py``: ``await ctx.db.refresh(row)`` right after ``flush()``.

These tests simulate the trigger by bumping ``updated_at`` inside ``refresh``
(no Postgres), so they pin the refresh-before-serialize ordering. The real
trigger path is covered by the testcontainer migration suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.addresses import UpdateAddressInput, update_address
from contact_ops.mcp.tools.emails import UpdateEmailInput, update_email
from contact_ops.mcp.tools.employment import UpdateEmploymentInput, update_employment
from contact_ops.mcp.tools.phones import UpdatePhoneInput, update_phone
from contact_ops.models import Email, Person, PersonOrgRole, Phone, PostalAddress

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
POST_WRITE = datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC)


class _FakeDb:
    """Answers ``get(Model, id)`` from a per-model row map; everything else no-ops."""

    def __init__(self, rows: dict[type, Any], bump_to: datetime | None = None) -> None:
        self._rows = rows
        self._bump_to = bump_to

    async def get(self, model: type, _ident: uuid.UUID) -> Any:
        return self._rows.get(model)

    async def flush(self) -> None:
        return None

    def add(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def refresh(self, row: Any) -> None:
        # In a real DB ``refresh`` reloads the row from the store; the fake
        # simulates the touch_updated trigger by bumping updated_at.
        if self._bump_to is not None:
            row.updated_at = self._bump_to

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
        extension=None,
        type=SimpleNamespace(value="mobile"),
        label=None,
        is_primary=True,
        is_verified=False,
        verified_at=None,
        carrier=None,
        valid_until=None,
    )


def _address(updated_at: datetime = NOW) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        organization_id=None,
        updated_at=updated_at,
        is_primary=True,
        street_address="1 Main St",
        locality="Charleston",
        region="SC",
        postal_code="29401",
        country_code="US",
        valid_until=None,
    )


def _employment(updated_at: datetime = NOW) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        updated_at=updated_at,
        role_type=SimpleNamespace(value="employee"),
        title="Engineer",
        department=None,
        seniority=SimpleNamespace(value="unknown"),
        employment_type=None,
        is_primary=True,
        started_at=None,
        ended_at=None,
        ownership_percent=None,
        equity_class=None,
    )


# ── post-write etag is returned (refresh after flush) ───────────────────────


@pytest.mark.asyncio
async def test_update_email_returns_post_write_etag() -> None:
    row = _email()
    pre = row.updated_at.isoformat()
    ctx = _ctx(_FakeDb({Email: row, Person: _person()}, bump_to=POST_WRITE), "email:write")

    out = await update_email(ctx, UpdateEmailInput(email_id=row.id, etag=pre, label="new-label"))
    assert out.changed_fields == ["label"]
    assert out.etag == POST_WRITE.isoformat()
    assert out.etag != pre


@pytest.mark.asyncio
async def test_update_phone_returns_post_write_etag() -> None:
    row = _phone()
    pre = row.updated_at.isoformat()
    ctx = _ctx(_FakeDb({Phone: row, Person: _person()}, bump_to=POST_WRITE), "phone:write")

    out = await update_phone(ctx, UpdatePhoneInput(phone_id=row.id, etag=pre, label="new-label"))
    assert out.changed_fields == ["label"]
    assert out.etag == POST_WRITE.isoformat()
    assert out.etag != pre


@pytest.mark.asyncio
async def test_update_address_returns_post_write_etag() -> None:
    row = _address()
    pre = row.updated_at.isoformat()
    ctx = _ctx(
        _FakeDb({PostalAddress: row, Person: _person()}, bump_to=POST_WRITE), "address:write"
    )

    out = await update_address(
        ctx, UpdateAddressInput(address_id=row.id, etag=pre, locality="North Charleston")
    )
    assert out.changed_fields == ["locality"]
    assert out.etag == POST_WRITE.isoformat()
    assert out.etag != pre


@pytest.mark.asyncio
async def test_update_employment_returns_post_write_etag() -> None:
    row = _employment()
    pre = row.updated_at.isoformat()
    ctx = _ctx(
        _FakeDb({PersonOrgRole: row, Person: _person()}, bump_to=POST_WRITE),
        "org:write person:write",
    )

    out = await update_employment(
        ctx, UpdateEmploymentInput(employment_id=row.id, etag=pre, title="Staff Engineer")
    )
    assert out.changed_fields == ["title"]
    assert out.etag == POST_WRITE.isoformat()
    assert out.etag != pre


# ── no-op updates also serialize the refreshed etag ─────────────────────────


@pytest.mark.asyncio
async def test_update_email_no_change_still_refreshes_etag() -> None:
    row = _email()
    pre = row.updated_at.isoformat()
    ctx = _ctx(_FakeDb({Email: row, Person: _person()}, bump_to=POST_WRITE), "email:write")

    out = await update_email(ctx, UpdateEmailInput(email_id=row.id, etag=pre))
    assert out.changed_fields == []
    assert out.etag == POST_WRITE.isoformat()
