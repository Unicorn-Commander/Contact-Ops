"""Non-DB tests for the REST PATCH routes added for email/phone/address/employment.

The MCP-native update tools (``update_email`` / ``update_phone`` /
``update_address`` / ``update_employment``) had no REST surface, so a stale etag
they raised (``STALE_ETAG`` → 409 in ``api/people.py``) could never surface over
HTTP. These tests drive the four new PATCH handlers directly with the ``_run_tool``
and ``_assert_owned_by_person`` seams monkeypatched, asserting the routes delegate
to the correct input model + tool handler and merge the path id into the payload —
no Postgres, no Docker.
"""

from __future__ import annotations

import uuid

import pytest

from contact_ops.api import people
from contact_ops.mcp.tools.addresses import UpdateAddressInput, update_address
from contact_ops.mcp.tools.emails import UpdateEmailInput, update_email
from contact_ops.mcp.tools.employment import UpdateEmploymentInput, update_employment
from contact_ops.mcp.tools.phones import UpdatePhoneInput, update_phone
from contact_ops.models import Email, PersonOrgRole, Phone, PostalAddress


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    handler,
    person_id: uuid.UUID,
    row_id: uuid.UUID,
    body: dict,
) -> tuple[list, list]:
    tool_calls: list = []
    guard_calls: list = []

    async def _fake_run_tool(request, input_model, tool_handler, payload):
        tool_calls.append((request, input_model, tool_handler, payload))
        return {"ok": True}

    async def _fake_assert(request, pid, model, rid):
        guard_calls.append((request, pid, model, rid))
        return None

    monkeypatch.setattr(people, "_run_tool", _fake_run_tool)
    monkeypatch.setattr(people, "_assert_owned_by_person", _fake_assert)

    await handler("REQUEST", person_id, row_id, body)
    return tool_calls, guard_calls


@pytest.mark.asyncio
async def test_update_person_email_delegates_to_update_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person_id = uuid.uuid4()
    email_id = uuid.uuid4()
    body = {"etag": "abc", "label": "home"}

    tool_calls, guard_calls = await _run(
        monkeypatch, people.update_person_email, person_id, email_id, body
    )

    assert guard_calls == [("REQUEST", person_id, Email, email_id)]
    assert tool_calls[0][1] is UpdateEmailInput
    assert tool_calls[0][2] is update_email
    assert tool_calls[0][3] == {**body, "email_id": email_id}


@pytest.mark.asyncio
async def test_update_person_phone_delegates_to_update_phone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person_id = uuid.uuid4()
    phone_id = uuid.uuid4()
    body = {"etag": "abc", "type": "mobile"}

    tool_calls, guard_calls = await _run(
        monkeypatch, people.update_person_phone, person_id, phone_id, body
    )

    assert guard_calls == [("REQUEST", person_id, Phone, phone_id)]
    assert tool_calls[0][1] is UpdatePhoneInput
    assert tool_calls[0][2] is update_phone
    assert tool_calls[0][3] == {**body, "phone_id": phone_id}


@pytest.mark.asyncio
async def test_update_person_address_delegates_to_update_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person_id = uuid.uuid4()
    address_id = uuid.uuid4()
    body = {"etag": "abc", "locality": "Charleston"}

    tool_calls, guard_calls = await _run(
        monkeypatch, people.update_person_address, person_id, address_id, body
    )

    assert guard_calls == [("REQUEST", person_id, PostalAddress, address_id)]
    assert tool_calls[0][1] is UpdateAddressInput
    assert tool_calls[0][2] is update_address
    assert tool_calls[0][3] == {**body, "address_id": address_id}


@pytest.mark.asyncio
async def test_update_person_employment_delegates_to_update_employment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person_id = uuid.uuid4()
    employment_id = uuid.uuid4()
    body = {"etag": "abc", "title": "CEO"}

    tool_calls, guard_calls = await _run(
        monkeypatch, people.update_person_employment, person_id, employment_id, body
    )

    assert guard_calls == [("REQUEST", person_id, PersonOrgRole, employment_id)]
    assert tool_calls[0][1] is UpdateEmploymentInput
    assert tool_calls[0][2] is update_employment
    assert tool_calls[0][3] == {**body, "employment_id": employment_id}


def test_patch_routes_are_registered() -> None:
    paths = {
        (tuple(sorted(getattr(r, "methods", []))), r.path)
        for r in people.router.routes
        if getattr(r, "methods", None)
    }
    patch_paths = {p for m, p in paths if m == ("PATCH",)}
    expected = {
        "/api/people/{person_id}/emails/{email_id}",
        "/api/people/{person_id}/phones/{phone_id}",
        "/api/people/{person_id}/addresses/{address_id}",
        "/api/people/{person_id}/employment/{employment_id}",
    }
    assert expected <= patch_paths
