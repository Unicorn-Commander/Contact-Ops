"""MCP tools for user-scoped Personal Access Tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, Field
from sqlalchemy import select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register
from contact_ops.models.personal_access_token import PersonalAccessToken

TOKEN_PREFIX = "co_pat_"  # noqa: S105 - token identifier prefix, not a secret.
TOKEN_RANDOM_CHARS = 40


class IssuePersonalAccessTokenInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    scopes: list[str] | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class IssuePersonalAccessTokenOutput(ToolOutput):
    pat_id: uuid.UUID
    token_plaintext: str
    last_4: str
    display_name: str
    scopes: list[str]
    expires_at: datetime | None
    created_at: datetime
    event_id: uuid.UUID | None = None


class PersonalAccessTokenSummary(BaseModel):
    id: uuid.UUID
    display_name: str
    last_4: str
    scopes: list[str]
    expires_at: datetime | None
    created_at: datetime
    last_used_at: datetime | None


class ListPersonalAccessTokensInput(BaseModel):
    pass


class ListPersonalAccessTokensOutput(ToolOutput):
    items: list[PersonalAccessTokenSummary]


class RevokePersonalAccessTokenInput(BaseModel):
    pat_id: uuid.UUID


class RevokePersonalAccessTokenOutput(ToolOutput):
    pat_id: uuid.UUID
    revoked_at: datetime
    status: str = "revoked"
    event_id: uuid.UUID | None = None


async def issue_personal_access_token(
    ctx: MCPContext,
    req: IssuePersonalAccessTokenInput,
) -> IssuePersonalAccessTokenOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("pat:write",))

    plaintext = _new_plaintext_token()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=req.expires_in_days) if req.expires_in_days else None
    row = PersonalAccessToken(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        user_uc_uid=_uc_uid(ctx),
        display_name=req.display_name,
        token_hash=_token_hash(plaintext),
        last_4=plaintext[-4:],
        scopes=list(req.scopes or []),
        expires_at=expires_at,
    )
    ctx.db.add(row)
    await ctx.db.flush()

    event_id = await emit_action_event(
        ctx,
        event_type="personal_access_token.issue",
        aggregate_type="tenant",
        aggregate_id=ctx.tenant_id,
        affected_ids=[row.id],
        payload_before=None,
        payload_after={
            "pat_id": str(row.id),
            "display_name": row.display_name,
            "last_4": row.last_4,
            "scopes": list(row.scopes or []),
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        },
    )
    return IssuePersonalAccessTokenOutput(
        pat_id=row.id,
        token_plaintext=plaintext,
        last_4=row.last_4,
        display_name=row.display_name,
        scopes=list(row.scopes or []),
        expires_at=row.expires_at,
        created_at=row.created_at or now,
        event_id=event_id,
    )


async def list_personal_access_tokens(
    ctx: MCPContext,
    _req: ListPersonalAccessTokensInput,
) -> ListPersonalAccessTokensOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("pat:read",))
    rows = (
        await ctx.db.execute(
            select(PersonalAccessToken)
            .where(
                PersonalAccessToken.tenant_id == ctx.tenant_id,
                PersonalAccessToken.user_uc_uid == _uc_uid(ctx),
                PersonalAccessToken.revoked_at.is_(None),
            )
            .order_by(PersonalAccessToken.created_at.desc())
        )
    ).scalars()
    return ListPersonalAccessTokensOutput(
        items=[
            PersonalAccessTokenSummary(
                id=row.id,
                display_name=row.display_name,
                last_4=row.last_4,
                scopes=list(row.scopes or []),
                expires_at=row.expires_at,
                created_at=row.created_at,
                last_used_at=row.last_used_at,
            )
            for row in rows
        ]
    )


async def revoke_personal_access_token(
    ctx: MCPContext,
    req: RevokePersonalAccessTokenInput,
) -> RevokePersonalAccessTokenOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("pat:write",))
    row = await ctx.db.get(PersonalAccessToken, req.pat_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.user_uc_uid != _uc_uid(ctx):
        raise ToolError(VALIDATION_FAILED, "personal access token not found", retryable=False)
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await ctx.db.flush()
        event_id = await emit_action_event(
            ctx,
            event_type="personal_access_token.revoke",
            aggregate_type="tenant",
            aggregate_id=ctx.tenant_id,
            affected_ids=[row.id],
            payload_before={"revoked_at": None},
            payload_after={"revoked_at": row.revoked_at.isoformat()},
        )
    else:
        event_id = None
    return RevokePersonalAccessTokenOutput(
        pat_id=row.id,
        revoked_at=row.revoked_at,
        event_id=event_id,
        status="already_revoked" if event_id is None else "revoked",
    )


def _new_plaintext_token() -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    return TOKEN_PREFIX + "".join(secrets.choice(alphabet) for _ in range(TOKEN_RANDOM_CHARS))


def _token_hash(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode()).digest()


def _uc_uid(ctx: MCPContext) -> str:
    return str(ctx.claims.get("uc_uid") or ctx.user_id)


for _name, _input, _output, _handler, _scopes, _read_only in [
    (
        "issue_personal_access_token",
        IssuePersonalAccessTokenInput,
        IssuePersonalAccessTokenOutput,
        issue_personal_access_token,
        ("pat:write",),
        False,
    ),
    (
        "list_personal_access_tokens",
        ListPersonalAccessTokensInput,
        ListPersonalAccessTokensOutput,
        list_personal_access_tokens,
        ("pat:read",),
        True,
    ),
    (
        "revoke_personal_access_token",
        RevokePersonalAccessTokenInput,
        RevokePersonalAccessTokenOutput,
        revoke_personal_access_token,
        ("pat:write",),
        False,
    ),
]:
    register(
        name=_name,
        description=f"{_name.replace('_', ' ')} for MCP bearer Personal Access Tokens.",
        input_model=cast(type[BaseModel], _input),
        output_model=cast(type[BaseModel], _output),
        handler=cast(Any, _handler),
        required_role="STAFF",
        required_scopes=_scopes,
        annotations={
            "readOnlyHint": bool(_read_only),
            "destructiveHint": _name.startswith("revoke"),
            "idempotentHint": _name.startswith(("list", "revoke")),
            "openWorldHint": False,
        },
        idempotency="none",
    )
