"""Notes / Log MCP tools."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError
from contact_ops.mcp.idempotency import check_or_register, store_result
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import register, stable_hash
from contact_ops.models.note import Note

NOTE_NOT_FOUND = "NOTE_NOT_FOUND"
NOTE_DELETE_FORBIDDEN = "NOTE_DELETE_FORBIDDEN"
NOTE_AUTHOR_OVERRIDE_CLAIM = "_contact_ops_note_author"

TargetType = Literal["user", "person", "org"]
AuthorType = Literal["user", "agent"]


class NoteRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target_type: TargetType
    target_id: str
    author_type: AuthorType
    author_id: str
    text: str
    source: dict[str, Any] | None = None
    created_at: datetime


class ListNotesInput(BaseModel):
    target_type: TargetType = "user"
    target_id: str | None = Field(default=None, min_length=1, max_length=400)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ListNotesOutput(BaseModel):
    items: list[NoteRecord]
    count: int


class AppendNoteInput(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    target_type: TargetType = "user"
    target_id: str | None = Field(default=None, min_length=1, max_length=400)
    source: dict[str, Any] | None = None
    idempotency_key: uuid.UUID | None = None


class AppendNoteOutput(NoteRecord):
    pass


class DeleteNoteInput(BaseModel):
    note_id: uuid.UUID


class DeleteNoteOutput(BaseModel):
    note_id: uuid.UUID
    deleted: bool


async def list_notes(ctx: MCPContext, req: ListNotesInput) -> ListNotesOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("notes:read",))
    target_id = _resolve_target_id(ctx, req.target_type, req.target_id)
    result = await ctx.db.execute(
        select(Note)
        .where(
            Note.tenant_id == ctx.tenant_id,
            Note.target_type == req.target_type,
            Note.target_id == target_id,
        )
        .order_by(Note.created_at.desc())
        .limit(req.limit)
        .offset(req.offset)
    )
    items = [_to_record(note) for note in result.scalars()]
    return ListNotesOutput(items=items, count=len(items))


async def append_note(ctx: MCPContext, req: AppendNoteInput) -> AppendNoteOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("notes:write",))
    target_id = _resolve_target_id(ctx, req.target_type, req.target_id)
    cached = await check_or_register(
        ctx,
        idempotency_key=str(req.idempotency_key) if req.idempotency_key else None,
        tool_name="append_note",
        stable_args_hash=stable_hash(req),
    )
    if cached is not None:
        return AppendNoteOutput.model_validate(cached)

    author_type, author_id = _resolve_author(ctx)
    note = Note(
        tenant_id=ctx.tenant_id,
        target_type=req.target_type,
        target_id=target_id,
        author_type=author_type,
        author_id=author_id,
        text=req.text,
        source=req.source,
    )
    ctx.db.add(note)
    await ctx.db.flush()
    await ctx.db.refresh(note)
    output = AppendNoteOutput.model_validate(_to_record(note))
    if req.idempotency_key:
        await store_result(ctx, str(req.idempotency_key), output.model_dump(mode="json"))
    return output


async def delete_note(ctx: MCPContext, req: DeleteNoteInput) -> DeleteNoteOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("notes:write",))
    note = await ctx.db.scalar(
        select(Note).where(Note.tenant_id == ctx.tenant_id, Note.id == req.note_id)
    )
    if note is None:
        raise ToolError(NOTE_NOT_FOUND, "note not found", retryable=False)
    caller_id = _caller_user_id(ctx)
    if note.author_type != "user" or note.author_id != caller_id:
        raise ToolError(
            NOTE_DELETE_FORBIDDEN,
            "only the original user author can delete a user-authored note",
            retryable=False,
        )
    await ctx.db.execute(
        delete(Note).where(Note.tenant_id == ctx.tenant_id, Note.id == req.note_id)
    )
    return DeleteNoteOutput(note_id=req.note_id, deleted=True)


def _resolve_target_id(ctx: MCPContext, target_type: TargetType, target_id: str | None) -> str:
    if target_id:
        return target_id
    if target_type == "user":
        return _caller_user_id(ctx)
    raise ToolError(
        VALIDATION_FAILED,
        f"target_id is required for target_type='{target_type}'",
        retryable=False,
    )


def _resolve_author(ctx: MCPContext) -> tuple[AuthorType, str]:
    override = ctx.claims.get(NOTE_AUTHOR_OVERRIDE_CLAIM)
    if isinstance(override, dict):
        author_type = override.get("author_type")
        author_id = override.get("author_id")
        if author_type == "agent" and isinstance(author_id, str) and author_id:
            return "agent", author_id
    return "user", _caller_user_id(ctx)


def _caller_user_id(ctx: MCPContext) -> str:
    return str(ctx.claims.get("uc_uid") or ctx.user_id)


def _to_record(note: Note) -> NoteRecord:
    return NoteRecord(
        id=note.id,
        target_type=note.target_type,
        target_id=note.target_id,
        author_type=note.author_type,
        author_id=note.author_id,
        text=note.text,
        source=note.source,
        created_at=note.created_at,
    )


def register_notes_tools() -> None:
    base = {"destructiveHint": False, "openWorldHint": False}
    register(
        name="list_notes",
        description=(
            "List timestamped notes for a target, newest first. Defaults to the "
            "caller user's journal when target_type='user' and target_id is omitted."
        ),
        input_model=ListNotesInput,
        output_model=ListNotesOutput,
        handler=list_notes,
        required_role="CLIENT",
        required_scopes=("notes:read",),
        annotations={**base, "readOnlyHint": True, "idempotentHint": True},
        idempotency="none",
    )
    register(
        name="append_note",
        description=(
            "Append a timestamped note to a target log. Author is derived from "
            "trusted request context, never from arguments."
        ),
        input_model=AppendNoteInput,
        output_model=AppendNoteOutput,
        handler=append_note,
        required_role="CLIENT",
        required_scopes=("notes:write",),
        annotations={**base, "readOnlyHint": False, "idempotentHint": True},
        idempotency="idempotency-key",
    )
    register(
        name="delete_note",
        description=(
            "Delete a note only when the caller is the original user author. "
            "Agent-authored notes are immutable from the user side."
        ),
        input_model=DeleteNoteInput,
        output_model=DeleteNoteOutput,
        handler=delete_note,
        required_role="CLIENT",
        required_scopes=("notes:write",),
        annotations={**base, "readOnlyHint": False, "idempotentHint": True},
        idempotency="none",
    )


register_notes_tools()
