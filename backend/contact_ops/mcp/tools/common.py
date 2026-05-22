"""Shared helpers for MCP tool implementations."""
# ruff: noqa: I001

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from contact_ops.mcp.errors import PERSON_MERGED, PERSON_NOT_FOUND, ToolError
from contact_ops.mcp.registry import MCPContext, ToolDef, register_tool
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.models import Person
from contact_ops.models.enums import MergeStatus


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class PersonSummary(BaseModel):
    person_id: uuid.UUID
    display_name: str
    primary_email: str | None = None
    current_org_id: uuid.UUID | None = None
    current_org_name: str | None = None
    last_interaction_at: datetime | None = None
    communication_strength: float | None = None
    tags: list[str] = Field(default_factory=list)
    etag: str | None = None


def stable_hash(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json", exclude_none=True)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).digest()


def now_utc() -> datetime:
    return datetime.now(UTC)


async def load_person(ctx: MCPContext, person_id: uuid.UUID) -> Person:
    person = await ctx.db.get(Person, person_id)
    if person is None or person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PERSON_NOT_FOUND, "person not found", retryable=False)
    if person.merge_status == MergeStatus.merged_into:
        raise ToolError(
            PERSON_MERGED,
            "person has been merged into another record",
            retryable=False,
            hint=str(person.merged_into_id) if person.merged_into_id else None,
        )
    return person


def person_summary(
    person: Person,
    primary_email: str | None = None,
    tags: list[str] | None = None,
) -> PersonSummary:
    return PersonSummary(
        person_id=person.id,
        display_name=person.display_name,
        primary_email=primary_email,
        current_org_id=person.current_org_id,
        last_interaction_at=person.last_interaction_at,
        communication_strength=float(person.communication_strength_score or 0),
        tags=tags or [],
        etag=person.etag,
    )


def register(
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    handler: Any,
    required_role: str,
    required_scopes: tuple[str, ...],
    annotations: dict[str, bool],
    idempotency: str,
) -> None:
    register_tool(
        ToolDef(
            name=name,
            description=description,
            input_model=input_model,
            output_model=output_model,
            handler=handler,
            required_role=required_role,
            required_scopes=required_scopes,
            annotations=annotations,
            idempotency=idempotency,
        )
    )


def enforce_tool_rbac(ctx: MCPContext, tool: ToolDef) -> None:
    require_role(ctx, tool.required_role)
    require_scopes(ctx, tool.required_scopes)


async def primary_email_for(ctx: MCPContext, person_id: uuid.UUID) -> str | None:
    from contact_ops.models import Email

    return cast(
        str | None,
        await ctx.db.scalar(
            select(Email.address)
            .where(Email.person_id == person_id)
            .order_by(Email.is_primary.desc(), Email.created_at.asc())
            .limit(1)
        ),
    )
