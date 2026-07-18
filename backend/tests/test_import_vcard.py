from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from contact_ops.api import import_vcard as import_vcard_api
from contact_ops.main import app
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.import_vcard import ProposeVCardRecordsInput, propose_vcard_records
from contact_ops.services import import_propose
from contact_ops.services.vcard_import import VCardImportError, propose_vcard_import

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

VCARD_ONE = """BEGIN:VCARD
VERSION:3.0
FN:Jane Example
EMAIL:REMOVED
TEL:+14155550100
ORG:Example Co
END:VCARD
"""

VCARD_TWO = """BEGIN:VCARD
VERSION:3.0
FN:John Example
EMAIL:REMOVED
TEL:+14155550101
END:VCARD
"""


def _ctx() -> MCPContext:
    return MCPContext(
        tenant_id=TENANT_ID,
        user_id="user-1",
        actor_chain={"sub": "user-1"},
        human_authority="user-1",
        # FakeSession so import_propose._load_existing_index() (the dup-detection
        # query) returns an empty index instead of touching a real DB.
        db=cast(Any, _FakeSession()),
        audit_db=cast(Any, None),
        request_id="test-request",
        claims={
            "sub": "user-1",
            "tenant_id": str(TENANT_ID),
            "realm_access": {"roles": ["STAFF"]},
            "scope": "person:write person:bulk",
        },
    )


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    from contact_ops.services import agents

    async def _noop_pipeline(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(agents, "run_dedup_agent", _noop_pipeline)
    monkeypatch.setattr(agents, "run_quality_filter", _noop_pipeline)
    monkeypatch.setattr(agents, "run_confidence_approver", _noop_pipeline)


@pytest.mark.asyncio
async def test_parse_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    proposed: list[str] = []
    monkeypatch.setattr(import_propose, "emit_person_create_proposal", _capture_emit(proposed))
    _stub_pipeline(monkeypatch)

    result = await propose_vcard_import(ctx=_ctx(), vcard_text=VCARD_ONE + VCARD_TWO)

    assert result.parsed_count == 2
    assert result.proposed_count == 2
    assert result.deduped_count == 0
    assert result.skipped_count == 0
    assert result.preview[0].display_name == "Jane Example"
    assert result.preview[0].emails == ["REMOVED"]
    assert result.preview[0].phones == ["+14155550100"]
    assert result.preview[0].organization == "Example Co"
    assert proposed == ["Jane Example", "John Example"]


@pytest.mark.asyncio
async def test_parse_error_on_non_vcard() -> None:
    with pytest.raises(VCardImportError):
        await propose_vcard_import(ctx=_ctx(), vcard_text="not a vcard")


@pytest.mark.asyncio
async def test_dedupe_within_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    proposed: list[str] = []
    monkeypatch.setattr(import_propose, "emit_person_create_proposal", _capture_emit(proposed))
    _stub_pipeline(monkeypatch)

    result = await propose_vcard_import(ctx=_ctx(), vcard_text=VCARD_ONE + VCARD_ONE)

    assert result.parsed_count == 2
    assert result.proposed_count == 1
    assert result.deduped_count == 1
    assert proposed == ["Jane Example"]


@pytest.mark.asyncio
async def test_dry_run_skips_proposals(monkeypatch: pytest.MonkeyPatch) -> None:
    proposed: list[str] = []
    monkeypatch.setattr(import_propose, "emit_person_create_proposal", _capture_emit(proposed))

    result = await propose_vcard_import(ctx=_ctx(), vcard_text=VCARD_ONE, dry_run=True)

    assert result.parsed_count == 1
    assert result.proposed_count == 0
    assert result.deduped_count == 0
    assert proposed == []


def test_rest_and_mcp_return_identical_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_standalone(True)
    monkeypatch.setattr(import_vcard_api, "async_session_maker", _session_factory())
    monkeypatch.setattr(import_vcard_api, "audit_session_maker", _session_factory())

    client = TestClient(app)
    response = client.post(
        "/import/vcard",
        files={"file": ("contacts.vcf", VCARD_ONE, "text/vcard")},
        data={"dry_run": "true"},
    )
    assert response.status_code == 200
    rest_payload = response.json()

    mcp_payload = _run_async(
        propose_vcard_records(_ctx(), ProposeVCardRecordsInput(vcard_text=VCARD_ONE, dry_run=True))
    ).model_dump(mode="json")
    assert set(rest_payload) == set(mcp_payload)
    assert rest_payload == mcp_payload


def test_mcp_tool_registered_and_chat_whitelisted() -> None:
    register_all_tools()
    assert get_tool("propose_vcard_records") is not None

    from contact_ops.services.chat_tools import ChatToolExecutor

    names = {
        definition["function"]["name"]
        for definition in ChatToolExecutor().definitions()
        if definition.get("type") == "function"
    }
    assert "propose_vcard_records" in names


def _capture_emit(
    proposed: list[str],
) -> Callable[..., Awaitable[uuid.UUID]]:
    async def _fake_emit(**kwargs: Any) -> uuid.UUID:
        proposed.append(kwargs["record"].display_name)
        return uuid.uuid4()

    return _fake_emit


def _session_factory() -> Any:
    @asynccontextmanager
    async def _context() -> AsyncGenerator[_FakeSession, None]:
        yield _FakeSession()

    return cast(Any, _context)


class _FakeSession:
    async def execute(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _set_standalone(value: bool) -> None:
    from contact_ops.core.config import get_settings

    get_settings().STANDALONE_MODE = value


def _run_async(awaitable: Awaitable[Any]) -> Any:
    import asyncio

    # Python 3.12: get_event_loop() raises in a sync context with no running
    # loop. Drive the coroutine on a fresh, self-contained loop instead.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(awaitable)
    finally:
        loop.close()
