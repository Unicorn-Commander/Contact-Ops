"""Primary preferences survive initial creation and non-destructive ingestion."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from contact_ops.mcp.tools.people import (
    CreatePersonInput,
    IMHandleInput,
    UpsertPersonInput,
    _ensure_im_handles,
    _insert_person,
)
from contact_ops.models import Email, IMHandle, Person, Phone

from .test_person_names import person_name_setup  # noqa: F401

VALUES = {
    "emails": [{"address": "REMOVED"}, {"address": "REMOVED"}],
    "phones": [{"e164": "+12025550101"}, {"e164": "+12025550102"}],
    "im_handles": [
        {"protocol": "signal", "handle": "first"},
        {"protocol": "signal", "handle": "second"},
    ],
}


@pytest.mark.parametrize("field", VALUES)
@pytest.mark.parametrize("upsert", [False, True])
def test_reject_multiple_explicit_primaries(field, upsert):
    args = {"display_name": "Synthetic", field: [dict(v, is_primary=True) for v in VALUES[field]]}
    model = CreatePersonInput
    if upsert:
        model = UpsertPersonInput
        args["match_by"] = [{"namespace": "synthetic", "value": "fixture"}]
    with pytest.raises(ValidationError, match="Only one primary"):
        model(**args)


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_create_stores_exactly_one_primary(db_session, person_name_setup, explicit):  # noqa: F811
    ctx = SimpleNamespace(
        db=db_session, tenant_id=person_name_setup["tenant_a"], user_id="synthetic"
    )
    args = {
        k: [dict(v, is_primary=(explicit and i == 1)) for i, v in enumerate(values)]
        for k, values in VALUES.items()
    }
    person = await _insert_person(ctx, CreatePersonInput(display_name="Synthetic", **args))
    for model, column in [(Email, Email.address), (Phone, Phone.e164), (IMHandle, IMHandle.handle)]:
        rows = (
            (
                await db_session.execute(
                    select(model).where(model.person_id == person.id).order_by(column)
                )
            )
            .scalars()
            .all()
        )
        assert [r.is_primary for r in rows] == ([False, True] if explicit else [True, False])


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_primary", [False, True])
async def test_upsert_prefers_explicit_new_handle_without_demoting_existing(
    db_session, person_name_setup, existing_primary  # noqa: F811
):  # noqa: F811
    person = await db_session.get(Person, person_name_setup["person_a"])
    if existing_primary:
        db_session.add(
            IMHandle(person_id=person.id, protocol="signal", handle="existing", is_primary=True)
        )
        await db_session.flush()
    ctx = SimpleNamespace(db=db_session)
    args = [
        IMHandleInput(protocol="signal", handle="first"),
        IMHandleInput(protocol="signal", handle="second", is_primary=True),
    ]
    added = await _ensure_im_handles(ctx, person, args, 1.0)
    await db_session.flush()
    assert [h.is_primary for h in added] == ([False, False] if existing_primary else [False, True])
    assert await _ensure_im_handles(ctx, person, args, 1.0) == []
    rows = (
        (await db_session.execute(select(IMHandle).where(IMHandle.person_id == person.id)))
        .scalars()
        .all()
    )
    assert sum(h.is_primary for h in rows) == 1
