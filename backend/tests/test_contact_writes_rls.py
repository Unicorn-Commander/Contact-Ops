"""Real non-BYPASSRLS write proof; audit transport is isolated in this test."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from test_person_names import person_name_setup  # noqa: F401

from contact_ops.mcp.errors import ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools import identifiers, im_handles, urls
from contact_ops.models import Identifier, IMHandle, Url


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["handle", "url", "identifier"])
async def test_contact_write_owner_and_foreign_tenant(
    db_session, person_name_setup, monkeypatch, kind  # noqa: F811
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
            im_handles.UpdateImHandleInput(im_handle_id=row_id, label="Updated"),
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
        update, req = urls.update_url, urls.UpdateUrlInput(url_id=row_id, label="Updated")
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
                identifier_id=row_id, url="https://example.invalid/id"
            ),
        )
        remove, remove_req = (
            identifiers.remove_identifier,
            identifiers.RemoveIdentifierInput(identifier_id=row_id),
        )
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
