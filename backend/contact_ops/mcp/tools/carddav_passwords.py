"""MCP tools for managing per-device CardDAV app passwords.

Three tools live here, all STAFF-required:

  * ``generate_carddav_app_password`` — mint a new app password for a
    device. The plaintext is returned ONCE in the response; only its
    bcrypt hash is persisted.
  * ``revoke_carddav_app_password`` — soft-delete by setting
    ``revoked_at``.
  * ``list_carddav_app_passwords`` — return metadata (device label,
    last_used_*, scopes, last 4 chars of the original plaintext) so
    the user can recognize and prune devices.

The tools register through the same :func:`register` helper used by
every other Phase 1 tool module so that they show up in ``tools/list``
and inherit the foundation's RBAC + audit emission patterns.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, Field
from sqlalchemy import select

from contact_ops.carddav.auth import hash_app_password
from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register
from contact_ops.models.carddav_app_password import CarddavAppPassword


# ---------- generate ----------


class GenerateAppPasswordInput(BaseModel):
    device_label: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(
        default_factory=lambda: ["carddav:read", "carddav:write"]
    )


class GenerateAppPasswordOutput(ToolOutput):
    app_password_id: uuid.UUID
    app_password_plaintext: str
    last_4_chars: str
    device_label: str
    scopes: list[str]
    created_at: datetime
    event_id: uuid.UUID | None = None


async def generate_carddav_app_password(
    ctx: MCPContext, req: GenerateAppPasswordInput
) -> GenerateAppPasswordOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("carddav:admin",))

    # 192-bit URL-safe token; ~32 ASCII chars after base64url. Long
    # enough to resist offline brute force, short enough that iOS's
    # password field doesn't truncate.
    plaintext = secrets.token_urlsafe(24)
    last_4 = plaintext[-4:]
    password_hash = hash_app_password(plaintext)

    row = CarddavAppPassword(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        device_label=req.device_label,
        password_hash=password_hash,
        last_4_chars=last_4,
        scopes=list(req.scopes),
        created_by_actor_id=_actor_uuid(ctx),
    )
    ctx.db.add(row)
    await ctx.db.flush()

    event_id = await emit_action_event(
        ctx,
        event_type="carddav.app_password.generate",
        aggregate_type="tenant",
        aggregate_id=ctx.tenant_id,
        affected_ids=[row.id],
        payload_before=None,
        payload_after={
            "app_password_id": str(row.id),
            "device_label": row.device_label,
            "last_4_chars": row.last_4_chars,
            "scopes": list(row.scopes),
        },
    )
    return GenerateAppPasswordOutput(
        app_password_id=row.id,
        app_password_plaintext=plaintext,
        last_4_chars=last_4,
        device_label=row.device_label,
        scopes=list(row.scopes),
        created_at=row.created_at or _now_utc(),
        event_id=event_id,
    )


# ---------- revoke ----------


class RevokeAppPasswordInput(BaseModel):
    app_password_id: uuid.UUID
    reason: Literal["lost_device", "rotated", "no_longer_used", "compromise", "other"] = (
        "rotated"
    )


class RevokeAppPasswordOutput(ToolOutput):
    app_password_id: uuid.UUID
    revoked_at: datetime
    status: str = "revoked"
    event_id: uuid.UUID | None = None


async def revoke_carddav_app_password(
    ctx: MCPContext, req: RevokeAppPasswordInput
) -> RevokeAppPasswordOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("carddav:admin",))

    row = await ctx.db.get(CarddavAppPassword, req.app_password_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise ToolError(VALIDATION_FAILED, "app password not found")
    if row.revoked_at is not None:
        return RevokeAppPasswordOutput(
            app_password_id=row.id,
            revoked_at=row.revoked_at,
            status="already_revoked",
        )

    row.revoked_at = _now_utc()
    row.revoked_by_actor_id = _actor_uuid(ctx)
    await ctx.db.flush()

    event_id = await emit_action_event(
        ctx,
        event_type="carddav.app_password.revoke",
        aggregate_type="tenant",
        aggregate_id=ctx.tenant_id,
        affected_ids=[row.id],
        payload_before={"revoked_at": None},
        payload_after={
            "revoked_at": row.revoked_at.isoformat(),
            "reason": req.reason,
        },
    )
    return RevokeAppPasswordOutput(
        app_password_id=row.id,
        revoked_at=row.revoked_at,
        event_id=event_id,
    )


# ---------- list ----------


class ListAppPasswordsInput(BaseModel):
    include_revoked: bool = False


class AppPasswordSummary(BaseModel):
    app_password_id: uuid.UUID
    device_label: str
    last_4_chars: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None = None
    last_used_user_agent: str | None = None
    last_used_ip: str | None = None
    revoked_at: datetime | None = None


class ListAppPasswordsOutput(ToolOutput):
    items: list[AppPasswordSummary]


async def list_carddav_app_passwords(
    ctx: MCPContext, req: ListAppPasswordsInput
) -> ListAppPasswordsOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("carddav:read",))

    stmt = select(CarddavAppPassword).where(
        CarddavAppPassword.tenant_id == ctx.tenant_id,
        CarddavAppPassword.user_id == ctx.user_id,
    )
    if not req.include_revoked:
        stmt = stmt.where(CarddavAppPassword.revoked_at.is_(None))
    stmt = stmt.order_by(CarddavAppPassword.created_at.desc())

    rows = (await ctx.db.execute(stmt)).scalars().all()
    items = [
        AppPasswordSummary(
            app_password_id=row.id,
            device_label=row.device_label,
            last_4_chars=row.last_4_chars,
            scopes=list(row.scopes or []),
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            last_used_user_agent=row.last_used_user_agent,
            last_used_ip=str(row.last_used_ip) if row.last_used_ip is not None else None,
            revoked_at=row.revoked_at,
        )
        for row in rows
    ]
    return ListAppPasswordsOutput(items=items)


# ---------- registration ----------


for _name, _input, _output, _handler, _scopes, _annotations in [
    (
        "generate_carddav_app_password",
        GenerateAppPasswordInput,
        GenerateAppPasswordOutput,
        generate_carddav_app_password,
        ("carddav:admin",),
        {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    ),
    (
        "revoke_carddav_app_password",
        RevokeAppPasswordInput,
        RevokeAppPasswordOutput,
        revoke_carddav_app_password,
        ("carddav:admin",),
        {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    ),
    (
        "list_carddav_app_passwords",
        ListAppPasswordsInput,
        ListAppPasswordsOutput,
        list_carddav_app_passwords,
        ("carddav:read",),
        {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    ),
]:
    register(
        name=_name,
        description=(
            f"{_name.replace('_', ' ')} — STAFF-only, scoped to the calling "
            "user's (tenant, uc_uid). Plaintext is shown only at generation."
        ),
        input_model=cast(type[BaseModel], _input),
        output_model=cast(type[BaseModel], _output),
        handler=_handler,
        required_role="STAFF",
        required_scopes=_scopes,
        annotations=_annotations,
        idempotency="none",
    )


# ---------- private helpers ----------


def _actor_uuid(ctx: MCPContext) -> uuid.UUID | None:
    try:
        return uuid.UUID(ctx.human_authority)
    except (TypeError, ValueError):
        return None


def _now_utc() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)
