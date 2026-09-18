"""Real non-BYPASSRLS write proof; audit transport is isolated in this test."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from contact_ops.mcp.errors import ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools import identifiers, im_handles, urls
from contact_ops.models import Identifier, IMHandle, Url

from .test_person_names import person_name_setup  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["handle", "url", "identifier"])
async def test_contact_write_owner_and_foreign_tenant(
    db_session,
    person_name_setup,  # noqa: F811
    monkeypatch,
    kind,  # noqa: F811
):
    s = person_name_setup
    conn = await db_session.connection()
    await conn.execute(text("SET LOCAL ROLE contact_ops_app"))
    assert await conn.scalar(
        text("SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user")
    )
    await conn.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(s["tenant_a"])}
    )
    audit = AsyncMock(return_value=None)
    for module in (im_handles, urls, identifiers):
        monkeypatch.setattr(module, "emit_action_event", audit)

    def ctx(tenant):
        return MCPContext(
            tenant_id=tenant,
            user_id="synthetic",
            actor_chain={"sub": "synthetic"},
            human_authority="synthetic",
            db=db_session,
            audit_db=db_session,
            request_id="rls-proof",
            claims={
                "realm_access": {"roles": ["STAFF"]},
                "scope": "person:write identifier:write identifier:verify",
            },
        )

    owner = ctx(s["tenant_a"])
    if kind == "handle":
        out = await im_handles.add_im_handle(
            owner,
            im_handles.AddImHandleInput(
                person_id=s["person_a"], protocol="signal", handle="synthetic"
            ),
        )
        model, row_id = IMHandle, out.im_handle_id
        update, req = (
            im_handles.update_im_handle,
            im_handles.UpdateImHandleInput(im_handle_id=row_id, etag="pending", label="Updated"),
        )
        remove, remove_req = (
            im_handles.remove_im_handle,
            im_handles.RemoveImHandleInput(im_handle_id=row_id),
        )
    elif kind == "url":
        out = await urls.add_url(
            owner, urls.AddUrlInput(person_id=s["person_a"], url="https://example.invalid/profile")
        )
        model, row_id = Url, out.url_id
        update, req = (
            urls.update_url,
            urls.UpdateUrlInput(url_id=row_id, etag="pending", label="Updated"),
        )
        remove, remove_req = urls.remove_url, urls.RemoveUrlInput(url_id=row_id)
    else:
        out = await identifiers.add_identifier(
            owner,
            identifiers.AddIdentifierInput(
                subject_kind="person",
                subject_id=s["person_a"],
                namespace="synthetic",
                value=str(uuid.uuid4()),
                verified=True,
            ),
        )
        model, row_id = Identifier, out.identifier_id
        update, req = (
            identifiers.update_identifier,
            identifiers.UpdateIdentifierInput(
                identifier_id=row_id, etag="pending", url="https://example.invalid/id"
            ),
        )
        remove, remove_req = (
            identifiers.remove_identifier,
            identifiers.RemoveIdentifierInput(identifier_id=row_id),
        )
    stored = await db_session.get(model, row_id)
    req.etag = stored.updated_at.isoformat()
    await update(owner, req)
    assert audit.await_count >= 2
    await conn.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(s["tenant_b"])}
    )
    db_session.expunge_all()
    assert await db_session.get(model, row_id) is None
    with pytest.raises(ToolError):
        await update(ctx(s["tenant_b"]), req)
    with pytest.raises(ToolError):
        await remove(ctx(s["tenant_b"]), remove_req)
    await conn.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(s["tenant_a"])}
    )
    db_session.expunge_all()
    assert await db_session.get(model, row_id) is not None
    await remove(owner, remove_req)
    assert await db_session.get(model, row_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["handle", "url", "identifier"])
async def test_contact_update_returns_usable_etag(db_session, person_name_setup, monkeypatch, kind):  # noqa: F811
    from datetime import UTC, datetime

    from contact_ops.mcp.errors import STALE_ETAG

    s = person_name_setup
    conn = await db_session.connection()
    await conn.execute(text("SET LOCAL ROLE contact_ops_app"))
    await conn.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(s["tenant_a"])}
    )
    old = datetime(2000, 1, 1, tzinfo=UTC)
    if kind == "handle":
        row = IMHandle(
            person_id=s["person_a"], protocol="signal", handle="synthetic", updated_at=old
        )
        module, update, request = (
            im_handles,
            im_handles.update_im_handle,
            im_handles.UpdateImHandleInput,
        )
        id_key, patch = "im_handle_id", {"label": "First"}
    elif kind == "url":
        row = Url(person_id=s["person_a"], url="https://example.invalid", updated_at=old)
        module, update, request = urls, urls.update_url, urls.UpdateUrlInput
        id_key, patch = "url_id", {"label": "First"}
    else:
        row = Identifier(
            person_id=s["person_a"], namespace="synthetic", value=str(uuid.uuid4()), updated_at=old
        )
        module, update, request = (
            identifiers,
            identifiers.update_identifier,
            identifiers.UpdateIdentifierInput,
        )
        id_key, patch = "identifier_id", {"url": "https://example.invalid/first"}
    db_session.add(row)
    await db_session.flush()
    monkeypatch.setattr(module, "emit_action_event", AsyncMock(return_value=None))
    ctx = MCPContext(
        tenant_id=s["tenant_a"],
        user_id="synthetic",
        actor_chain={},
        human_authority="synthetic",
        db=db_session,
        audit_db=db_session,
        request_id="etag-proof",
        claims={"realm_access": {"roles": ["STAFF"]}, "scope": "person:write identifier:write"},
    )
    args = {id_key: row.id, "etag": old.isoformat(), **patch}
    first = await update(ctx, request(**args))
    assert first.etag != old.isoformat()
    assert first.etag == row.updated_at.isoformat()
    # The server's returned token can be reused immediately; the old one cannot.
    await update(ctx, request(**{**args, "etag": first.etag}))
    with pytest.raises(ToolError) as exc:
        await update(ctx, request(**args))
    assert exc.value.code == STALE_ETAG
