"""Real database coverage for name writes and lossy CardDAV imports."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text

from contact_ops.carddav.addressbook import _sync_aka_names
from contact_ops.mcp.errors import ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools import person_names as names
from contact_ops.models import Person, PersonName

from .test_person_names import person_name_setup  # noqa: F401


@pytest.mark.asyncio
async def test_names_owner_lifecycle_and_foreign_denial(db_session, person_name_setup, monkeypatch):  # noqa: F811
    s = person_name_setup
    await db_session.execute(text("SET LOCAL ROLE contact_ops_app"))
    assert await db_session.scalar(
        text("SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user")
    )
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(s["tenant_a"])}
    )
    monkeypatch.setattr(names, "emit_action_event", AsyncMock(return_value=None))
    ctx = MCPContext(
        tenant_id=s["tenant_a"],
        user_id="synthetic",
        actor_chain={},
        human_authority="synthetic",
        db=db_session,
        audit_db=db_session,
        request_id="names-proof",
        claims={"realm_access": {"roles": ["STAFF"]}, "scope": "name:write name:read"},
    )
    first = await names.add_person_name(
        ctx,
        names.AddPersonNameInput(
            person_id=s["person_a"], name_type="nickname", value="Synthetic One"
        ),
    )
    second = await names.add_person_name(
        ctx,
        names.AddPersonNameInput(
            person_id=s["person_a"], name_type="nickname", value="Synthetic Two", is_primary=True
        ),
    )
    await names.update_person_name(
        ctx,
        names.UpdatePersonNameInput(person_name_id=first.person_name_id, value="Synthetic Three"),
    )
    person = await db_session.get(Person, s["person_a"])
    assert set(person.nicknames) == {"Synthetic Three", "Synthetic Two"}
    with pytest.raises(ToolError) as exc:
        await names.update_person_name(
            ctx,
            names.UpdatePersonNameInput(person_name_id=first.person_name_id, value="synthetic two"),
        )
    assert exc.value.code == "DUPLICATE_RECORD"
    await names.set_primary_person_name(
        ctx, names.PersonNameIdInput(person_name_id=first.person_name_id)
    )
    result = await names.list_person_names(ctx, names.ListPersonNamesInput(person_id=s["person_a"]))
    assert sum(row["is_primary"] for row in result.items) == 1
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(s["tenant_b"])}
    )
    db_session.expunge_all()
    ctx.tenant_id = s["tenant_b"]
    with pytest.raises(ToolError):
        await names.remove_person_name(
            ctx, names.RemovePersonNameInput(person_name_id=second.person_name_id)
        )
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(s["tenant_a"])}
    )
    ctx.tenant_id = s["tenant_a"]
    await names.remove_person_name(
        ctx, names.RemovePersonNameInput(person_name_id=second.person_name_id)
    )
    person = await db_session.get(Person, s["person_a"])
    assert person.nicknames == ["Synthetic Three"]


@pytest.mark.asyncio
async def test_carddav_aka_is_additive_and_deduplicated(db_session, person_name_setup):  # noqa: F811
    pid = person_name_setup["person_a"]
    await _sync_aka_names(db_session, pid, ["Synthetic First"])
    await _sync_aka_names(db_session, pid, [])
    await _sync_aka_names(db_session, pid, ["Synthetic Second", "synthetic second"])
    rows = (
        (await db_session.execute(select(PersonName).where(PersonName.person_id == pid)))
        .scalars()
        .all()
    )
    assert {r.value for r in rows} == {"Synthetic First", "Synthetic Second"}
    assert [r.value for r in rows if r.is_primary] == ["Synthetic First"]
