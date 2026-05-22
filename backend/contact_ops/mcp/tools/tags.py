from __future__ import annotations

import re
import uuid
from collections import Counter
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import BATCH_TOO_LARGE, VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, register
from contact_ops.models import Person, PersonTenantMembership, Tag

TAG_RE = re.compile(r"^[a-zA-Z0-9_\-\.\: ]+$")


def _slug(tag: str) -> str:
    normalized = re.sub(r"\s+", "-", tag.strip().lower())
    if not normalized or not TAG_RE.match(tag):
        raise ValueError(f"invalid tag: {tag}")
    return normalized


class TagPersonInput(BaseModel):
    person_id: uuid.UUID
    tags: list[str] = Field(min_length=1, max_length=20)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: list[str]) -> list[str]:
        return [_slug(tag) for tag in tags]


class TagPersonOutput(ToolOutput):
    person_id: uuid.UUID
    added: list[str]
    already_present: list[str] = Field(default_factory=list)
    status: str
    event_id: uuid.UUID | None = None


class UntagPersonInput(TagPersonInput):
    pass


class UntagPersonOutput(ToolOutput):
    person_id: uuid.UUID
    removed: list[str]
    not_present: list[str]
    status: str
    event_id: uuid.UUID | None = None


class ListTagsInput(BaseModel):
    contains: str | None = Field(default=None, max_length=40)
    min_count: int = Field(default=1, ge=1)
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = None


class ListTagsOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int
    next_cursor: str | None = None


class BulkTagInput(BaseModel):
    person_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    operation: Literal["add", "remove"] = "add"
    tags: list[str] = Field(min_length=1, max_length=20)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: list[str]) -> list[str]:
        return [_slug(tag) for tag in tags]


class BulkTagOutput(ToolOutput):
    updated_count: int
    results: list[dict[str, Any]]
    batch_event_id: uuid.UUID | None = None


class SearchByTagInput(BaseModel):
    tags_any: list[str] | None = None
    tags_all: list[str] | None = None
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None

    @field_validator("tags_any", "tags_all")
    @classmethod
    def normalize_tags(cls, tags: list[str] | None) -> list[str] | None:
        return [_slug(tag) for tag in tags] if tags else tags


class SearchByTagOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int
    next_cursor: str | None = None


async def _membership(ctx: MCPContext, person_id: uuid.UUID) -> PersonTenantMembership:
    await load_person(ctx, person_id)
    row = await ctx.db.get(
        PersonTenantMembership, {"person_id": person_id, "tenant_id": ctx.tenant_id}
    )
    if row is None:
        row = PersonTenantMembership(person_id=person_id, tenant_id=ctx.tenant_id, tags=[])
        ctx.db.add(row)
        await ctx.db.flush()
    return row


async def _ensure_tag_rows(ctx: MCPContext, tags: list[str]) -> None:
    for tag in tags:
        existing = await ctx.db.scalar(
            select(Tag.id).where(Tag.tenant_id == ctx.tenant_id, Tag.slug == tag)
        )
        if existing is None:
            ctx.db.add(Tag(tenant_id=ctx.tenant_id, slug=tag, display=tag))
    await ctx.db.flush()


async def tag_person(ctx: MCPContext, req: TagPersonInput) -> TagPersonOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["tag:write"])
    row = await _membership(ctx, req.person_id)
    before = sorted(row.tags or [])
    present = set(before)
    incoming = set(req.tags)
    added = sorted(incoming - present)
    already = sorted(incoming & present)
    if added:
        await _ensure_tag_rows(ctx, added)
        row.tags = sorted(present | incoming)
        await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="tag.person",
        aggregate_type="tag",
        aggregate_id=req.person_id,
        affected_ids=[req.person_id],
        payload_before={"tags": before},
        payload_after={"tags": row.tags, "added": added},
        confidence=req.confidence,
    )
    return TagPersonOutput(
        person_id=req.person_id,
        added=added,
        already_present=already,
        status="applied",
        event_id=event_id,
    )


async def untag_person(ctx: MCPContext, req: UntagPersonInput) -> UntagPersonOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["tag:write"])
    row = await _membership(ctx, req.person_id)
    before = sorted(row.tags or [])
    present = set(before)
    requested = set(req.tags)
    removed = sorted(present & requested)
    not_present = sorted(requested - present)
    if removed:
        row.tags = sorted(present - requested)
        await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="tag.person_remove",
        aggregate_type="tag",
        aggregate_id=req.person_id,
        affected_ids=[req.person_id],
        payload_before={"tags": before},
        payload_after={"tags": row.tags, "removed": removed},
        confidence=req.confidence,
    )
    return UntagPersonOutput(
        person_id=req.person_id,
        removed=removed,
        not_present=not_present,
        status="applied",
        event_id=event_id,
    )


async def list_tags(ctx: MCPContext, req: ListTagsInput) -> ListTagsOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["tag:read"])
    rows = (
        (
            await ctx.db.execute(
                select(PersonTenantMembership.tags).where(
                    PersonTenantMembership.tenant_id == ctx.tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    counts: Counter[str] = Counter()
    for tags in rows:
        counts.update(tags or [])
    tag_counts: list[tuple[str, int]] = sorted(
        (tag, count) for tag, count in counts.items() if count >= req.min_count
    )
    if req.contains:
        tag_counts = [(tag, count) for tag, count in tag_counts if req.contains.lower() in tag]
    if req.cursor:
        tag_counts = [(tag, count) for tag, count in tag_counts if tag > req.cursor]
    page = tag_counts[: req.limit]
    return ListTagsOutput(
        items=[
            {"tag": tag, "person_count": count, "org_count": 0, "last_applied_at": None}
            for tag, count in page
        ],
        count=len(page),
        next_cursor=page[-1][0] if len(tag_counts) > req.limit and page else None,
    )


async def bulk_tag(ctx: MCPContext, req: BulkTagInput) -> BulkTagOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["tag:write", "tag:bulk"])
    if len(req.person_ids) > 50:
        raise ToolError(BATCH_TOO_LARGE, "bulk_tag accepts at most 50 persons")
    results: list[dict[str, Any]] = []
    for person_id in req.person_ids:
        if req.operation == "add":
            add_result = await tag_person(
                ctx, TagPersonInput(person_id=person_id, tags=req.tags, confidence=req.confidence)
            )
            results.append(add_result.model_dump(mode="json"))
        else:
            remove_result = await untag_person(
                ctx, UntagPersonInput(person_id=person_id, tags=req.tags, confidence=req.confidence)
            )
            results.append(remove_result.model_dump(mode="json"))
    batch_event_id = await emit_action_event(
        ctx,
        event_type="tag.bulk",
        aggregate_type="tag",
        aggregate_id=req.person_ids[0],
        affected_ids=req.person_ids,
        payload_before=None,
        payload_after={"operation": req.operation, "tags": req.tags, "count": len(req.person_ids)},
        confidence=req.confidence,
    )
    return BulkTagOutput(updated_count=len(results), results=results, batch_event_id=batch_event_id)


async def search_by_tag(ctx: MCPContext, req: SearchByTagInput) -> SearchByTagOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["tag:read", "person:read"])
    tags_any = set(req.tags_any or [])
    tags_all = set(req.tags_all or [])
    if not tags_any and not tags_all:
        raise ToolError(VALIDATION_FAILED, "at least one of tags_any or tags_all is required")
    rows = (
        (
            await ctx.db.execute(
                select(PersonTenantMembership).where(
                    PersonTenantMembership.tenant_id == ctx.tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        row_tags = set(row.tags or [])
        if tags_any and not (row_tags & tags_any):
            continue
        if tags_all and not tags_all.issubset(row_tags):
            continue
        person = await ctx.db.get(Person, row.person_id)
        if person is None:
            continue
        items.append(
            {
                "subject_kind": "person",
                "subject_id": person.id,
                "summary": {"display_name": person.display_name, "etag": person.etag},
                "matched_tags": sorted(row_tags & (tags_any | tags_all)),
            }
        )
    if req.cursor:
        items = [item for item in items if str(item["subject_id"]) > req.cursor]
    page = items[: req.limit]
    return SearchByTagOutput(
        items=page,
        count=len(page),
        next_cursor=str(page[-1]["subject_id"]) if len(items) > req.limit and page else None,
    )


for _name, _input, _output, _handler, _role, _scopes, _readonly in [
    ("tag_person", TagPersonInput, TagPersonOutput, tag_person, "STAFF", ("tag:write",), False),
    (
        "untag_person",
        UntagPersonInput,
        UntagPersonOutput,
        untag_person,
        "STAFF",
        ("tag:write",),
        False,
    ),
    ("list_tags", ListTagsInput, ListTagsOutput, list_tags, "CLIENT", ("tag:read",), True),
    ("bulk_tag", BulkTagInput, BulkTagOutput, bulk_tag, "STAFF", ("tag:write", "tag:bulk"), False),
    (
        "search_by_tag",
        SearchByTagInput,
        SearchByTagOutput,
        search_by_tag,
        "CLIENT",
        ("tag:read", "person:read"),
        True,
    ),
]:
    register(
        name=_name,
        description=(
            f"{_name.replace('_', ' ')} for case-normalized tenant tags on people, "
            "with set semantics, RBAC, and action_event audit for mutations."
        ),
        input_model=cast(type[BaseModel], _input),
        output_model=cast(type[BaseModel], _output),
        handler=_handler,
        required_role=_role,
        required_scopes=_scopes,
        annotations={
            "readOnlyHint": _readonly,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
