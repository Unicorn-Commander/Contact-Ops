"""Non-DB tests for the im_handle / url / identifier optimistic-concurrency etag.

The update tools now require an ``etag`` (the row's ``updated_at``) and reject a
write whose etag no longer matches — the same guard email/phone updates already
have. These tests drive the guard with a fake DB (no Postgres), verifying:

  * the Update*Input models require an etag;
  * a stale etag is rejected with STALE_ETAG before any mutation;
  * a current etag is accepted and the new etag is returned.

The DB-backed paths (real touch_updated trigger bumping updated_at on UPDATE) are
covered by the migration roundtrip tests in the testcontainer suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from contact_ops.mcp.errors import STALE_ETAG, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.identifiers import (
    UpdateIdentifierInput,
    update_identifier,
)
from contact_ops.mcp.tools.im_handles import UpdateImHandleInput, update_im_handle
from contact_ops.mcp.tools.urls import UpdateUrlInput, update_url
from contact_ops.models import Identifier, IMHandle, Person, Url

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

    def add(self, _obj: Any) -> None:
        return None

    async def refresh(self, _row: Any) -> None:
        # No-op: in a real DB `refresh` reloads the row from the store. The
        # post-write bump is simulated by _TriggerDb below.
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
    return SimpleNamespace(canonical_owner_tenant_id=TENANT, merge_status=None)


def _ctx(db: _FakeDb, scopes: str = "person:write") -> MCPContext:
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


def _im_handle(updated_at: datetime = NOW) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        updated_at=updated_at,
        protocol="instagram",
        handle="@diesel",
        label=None,
        is_primary=True,
    )


def _url(updated_at: datetime = NOW) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        updated_at=updated_at,
        url="https://example.com",
        type="profile",
        label=None,
        is_primary=True,
    )


def _identifier(updated_at: datetime = NOW) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        organization_id=None,
        updated_at=updated_at,
        namespace="handle",
        value="diesel",
        url=None,
        verified=False,
    )


# ── input validation ────────────────────────────────────────────────────────


def test_update_inputs_require_etag() -> None:
    with pytest.raises(ValueError):
        UpdateImHandleInput(im_handle_id=uuid.uuid4())
    with pytest.raises(ValueError):
        UpdateUrlInput(url_id=uuid.uuid4())
    with pytest.raises(ValueError):
        UpdateIdentifierInput(identifier_id=uuid.uuid4())


# ── stale etag rejected ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_im_handle_rejects_stale_etag() -> None:
    row = _im_handle()
    ctx = _ctx(_FakeDb({IMHandle: row, Person: _person()}))
    req = UpdateImHandleInput(im_handle_id=row.id, etag="stale")

    with pytest.raises(ToolError) as exc:
        await update_im_handle(ctx, req)
    assert exc.value.code == STALE_ETAG


@pytest.mark.asyncio
async def test_update_url_rejects_stale_etag() -> None:
    row = _url()
    ctx = _ctx(_FakeDb({Url: row, Person: _person()}))
    req = UpdateUrlInput(url_id=row.id, etag="stale")

    with pytest.raises(ToolError) as exc:
        await update_url(ctx, req)
    assert exc.value.code == STALE_ETAG


@pytest.mark.asyncio
async def test_update_identifier_rejects_stale_etag() -> None:
    row = _identifier()
    ctx = _ctx(_FakeDb({Identifier: row, Person: _person()}), scopes="identifier:write")
    req = UpdateIdentifierInput(identifier_id=row.id, etag="stale")

    with pytest.raises(ToolError) as exc:
        await update_identifier(ctx, req)
    assert exc.value.code == STALE_ETAG


# ── current etag accepted + new etag returned ───────────────────────────────


@pytest.mark.asyncio
async def test_update_im_handle_accepts_current_etag() -> None:
    row = _im_handle()
    ctx = _ctx(_FakeDb({IMHandle: row, Person: _person()}))

    out = await update_im_handle(
        ctx, UpdateImHandleInput(im_handle_id=row.id, etag=row.updated_at.isoformat())
    )
    assert out.etag == row.updated_at.isoformat()
    assert out.changed_fields == []


@pytest.mark.asyncio
async def test_update_url_accepts_current_etag() -> None:
    row = _url()
    ctx = _ctx(_FakeDb({Url: row, Person: _person()}))

    out = await update_url(
        ctx, UpdateUrlInput(url_id=row.id, etag=row.updated_at.isoformat())
    )
    assert out.etag == row.updated_at.isoformat()
    assert out.changed_fields == []


@pytest.mark.asyncio
async def test_update_identifier_accepts_current_etag() -> None:
    row = _identifier()
    ctx = _ctx(_FakeDb({Identifier: row, Person: _person()}), scopes="identifier:write")

    out = await update_identifier(
        ctx, UpdateIdentifierInput(identifier_id=row.id, etag=row.updated_at.isoformat())
    )
    assert out.etag == row.updated_at.isoformat()
    assert out.changed_fields == []


# ── post-write etag (touch_updated trigger) ────────────────────────────────
#
# The `touch_updated` trigger bumps `updated_at` at the DB level on UPDATE, so
# the ORM row is one write behind. The update tools must `refresh()` the row
# after flush and return the POST-write etag, or a client that reuses the
# returned etag gets a false STALE_ETAG. These tests simulate the trigger by
# bumping `updated_at` inside `refresh`.


class _TriggerDb(_FakeDb):
    """Fake DB whose `refresh` simulates the touch_updated trigger bump."""

    def __init__(self, rows: dict[type, Any], bump_to: datetime) -> None:
        super().__init__(rows)
        self._bump_to = bump_to

    async def refresh(self, row: Any) -> None:
        row.updated_at = self._bump_to


POST_WRITE = datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_update_im_handle_returns_post_write_etag() -> None:
    row = _im_handle()
    pre = row.updated_at.isoformat()
    ctx = _ctx(_TriggerDb({IMHandle: row, Person: _person()}, bump_to=POST_WRITE))

    out = await update_im_handle(
        ctx, UpdateImHandleInput(im_handle_id=row.id, etag=pre, label="new-label")
    )
    assert out.changed_fields == ["label"]
    assert out.etag == POST_WRITE.isoformat()
    assert out.etag != pre


@pytest.mark.asyncio
async def test_update_url_returns_post_write_etag() -> None:
    row = _url()
    pre = row.updated_at.isoformat()
    ctx = _ctx(_TriggerDb({Url: row, Person: _person()}, bump_to=POST_WRITE))

    out = await update_url(ctx, UpdateUrlInput(url_id=row.id, etag=pre, label="new-label"))
    assert out.changed_fields == ["label"]
    assert out.etag == POST_WRITE.isoformat()
    assert out.etag != pre


@pytest.mark.asyncio
async def test_update_identifier_returns_post_write_etag() -> None:
    row = _identifier()
    pre = row.updated_at.isoformat()
    ctx = _ctx(
        _TriggerDb({Identifier: row, Person: _person()}, bump_to=POST_WRITE),
        scopes="identifier:write",
    )

    out = await update_identifier(
        ctx, UpdateIdentifierInput(identifier_id=row.id, etag=pre, url="https://x.co")
    )
    assert out.changed_fields == ["url"]
    assert out.etag == POST_WRITE.isoformat()
    assert out.etag != pre
