from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.services import import_propose
from contact_ops.services.csv_import import (
    CSVImportError,
    detect_csv_source_kind,
    propose_csv_import,
)

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

GOOGLE_CSV = (
    "First Name,Last Name,E-mail 1 - Value,Phone 1 - Value,Labels\n"
    "Jane,Example,REMOVED,+14155550100,* myContacts ::: Friends\n"
    "John,Example,REMOVED,,Coworkers\n"
)

GOOGLE_CSV_DUP_ROWS = (
    "First Name,Last Name,E-mail 1 - Value,Labels\n"
    "Jane,Example,REMOVED,Friends\n"
    "Jane,Example,REMOVED,Friends\n"
)

LINKEDIN_CSV = (
    "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
    "Sam,Linked,https://www.linkedin.com/in/sam,REMOVED,Acme,Engineer,01 Jan 2024\n"
)


def _ctx() -> MCPContext:
    return MCPContext(
        tenant_id=TENANT_ID,
        user_id="user-1",
        actor_chain={"sub": "user-1"},
        human_authority="user-1",
        db=cast(Any, None),
        audit_db=cast(Any, None),
        request_id="test-request",
        claims={
            "sub": "user-1",
            "tenant_id": str(TENANT_ID),
            "realm_access": {"roles": ["STAFF"]},
            "scope": "person:write person:bulk",
        },
    )


def _capture_emit(proposed: list[str]) -> Callable[..., Awaitable[uuid.UUID]]:
    async def _fake_emit(**kwargs: Any) -> uuid.UUID:
        proposed.append(kwargs["record"].display_name)
        return uuid.uuid4()

    return _fake_emit


def _existing_index(
    emails: dict[str, uuid.UUID] | None = None,
    phones: dict[str, uuid.UUID] | None = None,
) -> Callable[..., Awaitable[tuple[dict[str, uuid.UUID], dict[str, uuid.UUID]]]]:
    async def _fake_load(_ctx: Any) -> tuple[dict[str, uuid.UUID], dict[str, uuid.UUID]]:
        return (emails or {}, phones or {})

    return _fake_load


def _patch(monkeypatch: pytest.MonkeyPatch, proposed: list[str], **index: Any) -> None:
    from contact_ops.services import agents

    async def _noop_pipeline(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(import_propose, "emit_person_create_proposal", _capture_emit(proposed))
    monkeypatch.setattr(import_propose, "_load_existing_index", _existing_index(**index))
    monkeypatch.setattr(agents, "run_dedup_agent", _noop_pipeline)
    monkeypatch.setattr(agents, "run_quality_filter", _noop_pipeline)
    monkeypatch.setattr(agents, "run_confidence_approver", _noop_pipeline)


# --- format detection -------------------------------------------------------


def test_detect_google() -> None:
    assert detect_csv_source_kind(["First Name", "Last Name", "Labels", "E-mail 1 - Value"]) == "google_csv"


def test_detect_linkedin() -> None:
    assert detect_csv_source_kind(["First Name", "Last Name", "Connected On", "Company"]) == "linkedin_csv"


def test_detect_unrecognized() -> None:
    assert detect_csv_source_kind(["foo", "bar"]) is None


# --- propose pipeline -------------------------------------------------------


@pytest.mark.asyncio
async def test_google_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    proposed: list[str] = []
    _patch(monkeypatch, proposed)

    result = await propose_csv_import(ctx=_ctx(), csv_text=GOOGLE_CSV, filename="contacts.csv")

    assert result.parsed_count == 2
    assert result.proposed_count == 2
    assert result.deduped_count == 0
    assert result.duplicate_count == 0
    assert result.skipped_count == 0
    assert result.preview[0].display_name == "Jane Example"
    assert result.preview[0].emails == ["REMOVED"]
    assert result.preview[0].phones == ["+14155550100"]
    # System labels ("* myContacts") dropped; real label kept.
    assert proposed == ["Jane Example", "John Example"]


@pytest.mark.asyncio
async def test_linkedin_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    proposed: list[str] = []
    _patch(monkeypatch, proposed)

    result = await propose_csv_import(ctx=_ctx(), csv_text=LINKEDIN_CSV, filename="Connections.csv")

    assert result.parsed_count == 1
    assert result.proposed_count == 1
    assert result.preview[0].display_name == "Sam Linked"


@pytest.mark.asyncio
async def test_existing_person_skipped_as_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    proposed: list[str] = []
    # Jane already exists in this tenant.
    _patch(monkeypatch, proposed, emails={"REMOVED": uuid.uuid4()})

    result = await propose_csv_import(ctx=_ctx(), csv_text=GOOGLE_CSV)

    assert result.parsed_count == 2
    assert result.duplicate_count == 1
    assert result.proposed_count == 1  # only John is new
    assert result.preview[0].duplicate is True  # Jane flagged
    assert result.preview[1].duplicate is False  # John not
    assert proposed == ["John Example"]


@pytest.mark.asyncio
async def test_existing_person_matched_by_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    proposed: list[str] = []
    _patch(monkeypatch, proposed, phones={"+14155550100": uuid.uuid4()})

    result = await propose_csv_import(ctx=_ctx(), csv_text=GOOGLE_CSV)

    assert result.duplicate_count == 1  # Jane matched on phone
    assert proposed == ["John Example"]


@pytest.mark.asyncio
async def test_in_file_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    proposed: list[str] = []
    _patch(monkeypatch, proposed)

    result = await propose_csv_import(ctx=_ctx(), csv_text=GOOGLE_CSV_DUP_ROWS)

    assert result.parsed_count == 2
    assert result.deduped_count == 1
    assert result.proposed_count == 1
    assert proposed == ["Jane Example"]


@pytest.mark.asyncio
async def test_dry_run_skips_proposals(monkeypatch: pytest.MonkeyPatch) -> None:
    proposed: list[str] = []
    _patch(monkeypatch, proposed)

    result = await propose_csv_import(ctx=_ctx(), csv_text=GOOGLE_CSV, dry_run=True)

    assert result.parsed_count == 2
    assert result.proposed_count == 0
    assert proposed == []


@pytest.mark.asyncio
async def test_unrecognized_csv_raises() -> None:
    with pytest.raises(CSVImportError):
        await propose_csv_import(ctx=_ctx(), csv_text="foo,bar\n1,2\n")


@pytest.mark.asyncio
async def test_empty_csv_raises() -> None:
    with pytest.raises(CSVImportError):
        await propose_csv_import(ctx=_ctx(), csv_text="")


def test_mcp_tool_registered() -> None:
    register_all_tools()
    assert get_tool("propose_csv_records") is not None
