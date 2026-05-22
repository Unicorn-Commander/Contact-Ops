from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import DUPLICATE_RECORD, VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, now_utc, register
from contact_ops.mcp.tools.orgs import _load_org
from contact_ops.models import Identifier


class AddIdentifierInput(BaseModel):
    subject_kind: Literal["person", "org"]
    subject_id: uuid.UUID
    namespace: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=400)
    url: str | None = Field(default=None, max_length=1000)
    verified: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)


class IdentifierOutput(ToolOutput):
    identifier_id: uuid.UUID
    subject_kind: str
    subject_id: uuid.UUID
    namespace: str
    value: str
    verified: bool = False
    status: str = "applied"
    event_id: uuid.UUID | None = None


class RemoveIdentifierInput(BaseModel):
    identifier_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=400)
    confidence: float = Field(default=1.0, ge=0, le=1)


class RemoveIdentifierOutput(ToolOutput):
    identifier_id: uuid.UUID
    removed_at: datetime
    status: str
    event_id: uuid.UUID | None = None


class ListIdentifiersInput(BaseModel):
    subject_kind: Literal["person", "org"]
    subject_id: uuid.UUID
    namespace_any: list[str] | None = None
    verified_only: bool = False


class ListIdentifiersOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int


class VerifyIdentifierInput(BaseModel):
    identifier_id: uuid.UUID
    method: Literal["manual", "url_check", "oauth", "sms_code", "email_link"] = "manual"
    confidence: float = Field(default=1.0, ge=0, le=1)


class VerifyIdentifierOutput(ToolOutput):
    identifier_id: uuid.UUID
    verified: bool
    verified_via: str
    verified_at: datetime
    event_id: uuid.UUID | None = None


def _subject_columns(subject_kind: str, subject_id: uuid.UUID) -> dict[str, uuid.UUID | None]:
    return {
        "person_id": subject_id if subject_kind == "person" else None,
        "organization_id": subject_id if subject_kind == "org" else None,
    }


def _row_subject(row: Identifier) -> tuple[str, uuid.UUID]:
    if row.person_id is not None:
        return "person", row.person_id
    if row.organization_id is not None:
        return "org", row.organization_id
    raise ToolError(VALIDATION_FAILED, "identifier has no subject")


async def _assert_subject(ctx: MCPContext, subject_kind: str, subject_id: uuid.UUID) -> None:
    if subject_kind == "person":
        await load_person(ctx, subject_id)
    else:
        await _load_org(ctx, subject_id)


async def _load_identifier(ctx: MCPContext, identifier_id: uuid.UUID) -> Identifier:
    row = await ctx.db.get(Identifier, identifier_id)
    if row is None:
        raise ToolError(VALIDATION_FAILED, "identifier not found")
    kind, subject_id = _row_subject(row)
    await _assert_subject(ctx, kind, subject_id)
    return row


async def add_identifier(ctx: MCPContext, req: AddIdentifierInput) -> IdentifierOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["identifier:write"])
    await _assert_subject(ctx, req.subject_kind, req.subject_id)
    existing = await ctx.db.scalar(
        select(Identifier).where(
            Identifier.person_id == (req.subject_id if req.subject_kind == "person" else None),
            Identifier.organization_id == (req.subject_id if req.subject_kind == "org" else None),
            func.lower(Identifier.namespace) == req.namespace.lower(),
            func.lower(Identifier.value) == req.value.lower(),
        )
    )
    if existing:
        return IdentifierOutput(
            identifier_id=existing.id,
            subject_kind=req.subject_kind,
            subject_id=req.subject_id,
            namespace=existing.namespace,
            value=existing.value,
            verified=existing.verified,
        )
    other = await ctx.db.scalar(
        select(Identifier.id).where(
            func.lower(Identifier.namespace) == req.namespace.lower(),
            func.lower(Identifier.value) == req.value.lower(),
        )
    )
    if other:
        raise ToolError(DUPLICATE_RECORD, "identifier already belongs to another subject")
    row = Identifier(
        **_subject_columns(req.subject_kind, req.subject_id),
        namespace=req.namespace.strip().lower(),
        value=req.value.strip(),
        url=req.url,
        verified=req.verified,
        confidence=Decimal(str(req.confidence)),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="identifier.add",
        aggregate_type="identifier",
        aggregate_id=row.id,
        affected_ids=[req.subject_id],
        payload_before=None,
        payload_after={"namespace": row.namespace, "value": row.value, "verified": row.verified},
        confidence=req.confidence,
    )
    return IdentifierOutput(
        identifier_id=row.id,
        subject_kind=req.subject_kind,
        subject_id=req.subject_id,
        namespace=row.namespace,
        value=row.value,
        verified=row.verified,
        event_id=event_id,
    )


async def remove_identifier(ctx: MCPContext, req: RemoveIdentifierInput) -> RemoveIdentifierOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["identifier:write"])
    row = await _load_identifier(ctx, req.identifier_id)
    kind, subject_id = _row_subject(row)
    before = {"namespace": row.namespace, "value": row.value, "verified": row.verified}
    await ctx.db.delete(row)
    await ctx.db.flush()
    removed_at = now_utc()
    event_id = await emit_action_event(
        ctx,
        event_type="identifier.remove",
        aggregate_type="identifier",
        aggregate_id=req.identifier_id,
        affected_ids=[subject_id],
        payload_before=before,
        payload_after={
            "removed_at": removed_at.isoformat(),
            "subject_kind": kind,
            "reason": req.reason,
        },
        confidence=req.confidence,
    )
    return RemoveIdentifierOutput(
        identifier_id=req.identifier_id, removed_at=removed_at, status="applied", event_id=event_id
    )


async def list_identifiers(ctx: MCPContext, req: ListIdentifiersInput) -> ListIdentifiersOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["identifier:read"])
    await _assert_subject(ctx, req.subject_kind, req.subject_id)
    stmt = select(Identifier).where(
        Identifier.person_id == (req.subject_id if req.subject_kind == "person" else None),
        Identifier.organization_id == (req.subject_id if req.subject_kind == "org" else None),
    )
    if req.namespace_any:
        stmt = stmt.where(
            func.lower(Identifier.namespace).in_([ns.lower() for ns in req.namespace_any])
        )
    if req.verified_only:
        stmt = stmt.where(Identifier.verified.is_(True))
    rows = (await ctx.db.execute(stmt.order_by(Identifier.created_at.desc()))).scalars().all()
    return ListIdentifiersOutput(
        items=[
            {
                "identifier_id": row.id,
                "namespace": row.namespace,
                "value": row.value,
                "url": row.url,
                "verified": row.verified,
                "confidence": float(row.confidence),
            }
            for row in rows
        ],
        count=len(rows),
    )


async def verify_identifier(ctx: MCPContext, req: VerifyIdentifierInput) -> VerifyIdentifierOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["identifier:write", "identifier:verify"])
    row = await _load_identifier(ctx, req.identifier_id)
    before = {"verified": row.verified}
    row.verified = True
    await ctx.db.flush()
    verified_at = now_utc()
    event_id = await emit_action_event(
        ctx,
        event_type="identifier.verify",
        aggregate_type="identifier",
        aggregate_id=row.id,
        affected_ids=[],
        payload_before=before,
        payload_after={
            "verified": True,
            "verified_via": req.method,
            "verified_at": verified_at.isoformat(),
        },
        confidence=req.confidence,
    )
    return VerifyIdentifierOutput(
        identifier_id=row.id,
        verified=True,
        verified_via=req.method,
        verified_at=verified_at,
        event_id=event_id,
    )


for _name, _input, _output, _handler, _role, _scopes, _readonly in [
    (
        "add_identifier",
        AddIdentifierInput,
        IdentifierOutput,
        add_identifier,
        "STAFF",
        ("identifier:write",),
        False,
    ),
    (
        "remove_identifier",
        RemoveIdentifierInput,
        RemoveIdentifierOutput,
        remove_identifier,
        "STAFF",
        ("identifier:write",),
        False,
    ),
    (
        "list_identifiers",
        ListIdentifiersInput,
        ListIdentifiersOutput,
        list_identifiers,
        "CLIENT",
        ("identifier:read",),
        True,
    ),
    (
        "verify_identifier",
        VerifyIdentifierInput,
        VerifyIdentifierOutput,
        verify_identifier,
        "STAFF",
        ("identifier:write", "identifier:verify"),
        False,
    ),
]:
    register(
        name=_name,
        description=(
            f"{_name.replace('_', ' ')} for tenant-scoped person or organization "
            "identifiers with RBAC, audit logging, and structured results."
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
        idempotency="natural-key" if _name == "add_identifier" else "none",
    )
