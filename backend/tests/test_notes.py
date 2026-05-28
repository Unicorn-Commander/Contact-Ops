from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.mcp.errors import ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.notes import (
    AppendNoteInput,
    DeleteNoteInput,
    ListNotesInput,
    append_note,
    delete_note,
    list_notes,
)
from contact_ops.models.note import Note
from contact_ops.services import chat_tools
from contact_ops.services.chat_tools import ChatRequestContext, ChatToolExecutor

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER_ID = "user-123"


async def _seed_tenants(db: AsyncSession) -> None:
    await db.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix)
            VALUES
                (:tenant_id, 'notes-test', 'brand', 'Notes Test', :tenant_id,
                    'notes-test', 'notes-test'),
                (:other_tenant_id, 'notes-other', 'brand', 'Notes Other', :other_tenant_id,
                    'notes-other', 'notes-other')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"tenant_id": TENANT_ID, "other_tenant_id": OTHER_TENANT_ID},
    )
    await db.commit()


def _ctx(db: AsyncSession, *, tenant_id: uuid.UUID = TENANT_ID) -> MCPContext:
    return MCPContext(
        tenant_id=tenant_id,
        user_id=USER_ID,
        actor_chain={"sub": USER_ID},
        human_authority=USER_ID,
        db=db,
        audit_db=db,
        request_id="test-request",
        claims={
            "sub": USER_ID,
            "uc_uid": USER_ID,
            "realm_access": {"roles": ["CLIENT"]},
            "scope": "notes:read notes:write",
        },
    )


@pytest.mark.asyncio
async def test_notes_crud_defaults_to_current_user(db_session: AsyncSession) -> None:
    await _seed_tenants(db_session)
    ctx = _ctx(db_session)

    created = await append_note(ctx, AppendNoteInput(text="Investigated duplicate contacts."))
    assert created.target_type == "user"
    assert created.target_id == USER_ID
    assert created.author_type == "user"
    assert created.author_id == USER_ID

    listed = await list_notes(ctx, ListNotesInput())
    assert listed.count == 1
    assert listed.items[0].id == created.id

    deleted = await delete_note(ctx, DeleteNoteInput(note_id=created.id))
    assert deleted.deleted is True
    assert deleted.note_id == created.id

    listed_after_delete = await list_notes(ctx, ListNotesInput())
    assert listed_after_delete.items == []


@pytest.mark.asyncio
async def test_agent_authored_notes_are_immutable_to_user(db_session: AsyncSession) -> None:
    await _seed_tenants(db_session)
    ctx = _ctx(db_session)
    ctx.claims["_contact_ops_note_author"] = {
        "author_type": "agent",
        "author_id": "ai-contacts-analyst",
    }
    created = await append_note(ctx, AppendNoteInput(text="AI logged a completed analysis."))
    assert created.author_type == "agent"
    assert created.author_id == "ai-contacts-analyst"

    user_ctx = _ctx(db_session)
    with pytest.raises(ToolError) as exc:
        await delete_note(user_ctx, DeleteNoteInput(note_id=created.id))
    assert exc.value.code == "NOTE_DELETE_FORBIDDEN"


@pytest.mark.asyncio
async def test_notes_rls_limits_rows_to_current_tenant(
    db_session: AsyncSession,
) -> None:
    await _seed_tenants(db_session)
    db_session.add_all(
        [
            Note(
                tenant_id=TENANT_ID,
                target_type="user",
                target_id=USER_ID,
                author_type="user",
                author_id=USER_ID,
                text="visible",
            ),
            Note(
                tenant_id=OTHER_TENANT_ID,
                target_type="user",
                target_id=USER_ID,
                author_type="user",
                author_id=USER_ID,
                text="hidden",
            ),
        ]
    )
    await db_session.flush()
    await db_session.execute(text("SET LOCAL ROLE contact_ops_app"))
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(TENANT_ID)},
    )

    count = await db_session.scalar(select(func.count()).select_from(Note))
    visible_texts = (
        await db_session.execute(select(Note.text).order_by(Note.text.asc()))
    ).scalars().all()
    assert count == 1
    assert visible_texts == ["visible"]


def test_notes_tools_registered_and_chat_whitelist() -> None:
    register_all_tools()
    assert get_tool("list_notes") is not None
    assert get_tool("append_note") is not None
    assert get_tool("delete_note") is not None

    tool_names = {
        definition["function"]["name"]
        for definition in ChatToolExecutor().definitions()
        if definition.get("type") == "function"
    }
    assert {"list_notes", "append_note"} <= tool_names
    assert "delete_note" not in tool_names


@pytest.mark.asyncio
async def test_chat_append_note_uses_agent_author(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_tenants(db_session)
    monkeypatch.setattr(chat_tools, "async_session_maker", _session_factory(db_session))
    monkeypatch.setattr(chat_tools, "audit_session_maker", _session_factory(db_session))

    result = await ChatToolExecutor().execute(
        "append_note",
        {"text": "Logged from chat.", "source": {"action": "test"}},
        ChatRequestContext(
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            actor_chain={"sub": USER_ID},
            human_authority=USER_ID,
            claims={
                "sub": USER_ID,
                "uc_uid": USER_ID,
                "realm_access": {"roles": ["CLIENT"]},
                "scope": "notes:read notes:write",
            },
            request_id="chat-test",
        ),
    )

    assert result["author_type"] == "agent"
    assert result["author_id"] == "ai-contacts-analyst"
    assert result["target_id"] == USER_ID


def _session_factory(session: AsyncSession) -> Any:
    @asynccontextmanager
    async def _context() -> AsyncGenerator[AsyncSession, None]:
        yield session

    return cast(Any, _context)
