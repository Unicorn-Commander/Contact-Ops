"""Connector MCP tools."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast
from urllib.parse import urlencode

from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from contact_ops.connectors.base import (
    Connector,
    ConnectorRunResult,
    decrypt_payload,
    encrypt_payload,
    run_connector_pull,
)
from contact_ops.connectors.gmail import GmailConnector
from contact_ops.connectors.icloud import ICloudConnector, validate_icloud_credentials
from contact_ops.connectors.m365 import M365Connector
from contact_ops.connectors.oauth_state import create_state
from contact_ops.core.config import get_settings
from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import register

Provider = Literal["icloud", "m365", "gmail"]


class ConnectorRecord(BaseModel):
    id: uuid.UUID
    provider: Provider
    display_name: str
    configured_by: str
    status: str
    last_error: str | None = None
    configured_at: str
    last_pull_at: str | None = None
    last_pull_summary: dict[str, Any] | None = None


class ListConnectorsInput(BaseModel):
    # No args today, but Pydantic 2 refuses to validate against bare BaseModel
    # ("BaseModel cannot be instantiated directly") so the tool registry needs
    # a concrete empty subclass here.
    pass


class ListConnectorsOutput(BaseModel):
    # NOTE: field names match the ListResult<T> convention used elsewhere in the
    # codebase (frontend hooks expect `items` + `count`). Don't rename.
    items: list[ConnectorRecord]
    count: int


class ConfigureICloudConnectorInput(BaseModel):
    apple_id: EmailStr
    app_password: str = Field(min_length=8, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    idempotency_key: uuid.UUID | None = None


class ConfigureConnectorOutput(BaseModel):
    connector_id: uuid.UUID
    status: str


class OAuthStartInput(BaseModel):
    provider: Literal["m365", "gmail"]


class OAuthStartOutput(BaseModel):
    redirect_url: str
    state: str


class PullConnectorNowInput(BaseModel):
    connector_id: uuid.UUID
    dry_run: bool = False


class PullConnectorNowOutput(ConnectorRunResult):
    connector_run_id: uuid.UUID


class ListConnectorRunsInput(BaseModel):
    connector_id: uuid.UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ConnectorRunRecord(BaseModel):
    id: uuid.UUID
    connector_id: uuid.UUID
    # JOIN'd from connector_configs so the frontend RunsTable can show a chip
    # without a second lookup. Default to None for runs whose config has been
    # disconnected/deleted (LEFT JOIN result).
    provider: str | None = None
    display_name: str | None = None
    started_at: str
    completed_at: str | None = None
    status: str
    parsed_count: int
    proposed_count: int
    deduped_count: int
    skipped_count: int
    error_message: str | None = None
    triggered_by: str


class ListConnectorRunsOutput(BaseModel):
    # Same convention as ListConnectorsOutput — see note above.
    items: list[ConnectorRunRecord]
    count: int


class DisconnectConnectorInput(BaseModel):
    connector_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=500)


class DisconnectConnectorOutput(BaseModel):
    connector_id: uuid.UUID
    status: str


async def list_connectors(ctx: MCPContext, _req: ListConnectorsInput) -> ListConnectorsOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("connectors:read",))
    rows = await ctx.db.execute(
        text(
            """
            SELECT id, provider, display_name, configured_by, status, last_error,
                   configured_at, last_pull_at, last_pull_summary
            FROM connector_configs
            WHERE tenant_id = CAST(:tenant_id AS uuid)
            ORDER BY configured_at DESC
            """
        ),
        {"tenant_id": str(ctx.tenant_id)},
    )
    records = [
        ConnectorRecord(
            id=uuid.UUID(str(row["id"])),
            provider=row["provider"],
            display_name=row["display_name"],
            configured_by=row["configured_by"],
            status=row["status"],
            last_error=row["last_error"],
            configured_at=row["configured_at"].isoformat(),
            last_pull_at=row["last_pull_at"].isoformat() if row["last_pull_at"] else None,
            last_pull_summary=row["last_pull_summary"],
        )
        for row in rows.mappings()
    ]
    return ListConnectorsOutput(
        items=records,
        count=len(records),
    )


async def configure_icloud_connector(
    ctx: MCPContext,
    req: ConfigureICloudConnectorInput,
) -> ConfigureConnectorOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("connectors:write",))
    try:
        await validate_icloud_credentials(str(req.apple_id), req.app_password)
    except Exception as exc:
        raise ToolError(VALIDATION_FAILED, str(exc), retryable=False) from exc
    encrypted = encrypt_payload({"apple_id": str(req.apple_id), "app_password": req.app_password})
    row = await ctx.db.execute(
        text(
            """
            INSERT INTO connector_configs (
                tenant_id, provider, display_name, configured_by, encrypted_payload, status,
                last_error
            ) VALUES (
                CAST(:tenant_id AS uuid), 'icloud', :display_name, :configured_by,
                :encrypted_payload, 'configured', NULL
            )
            ON CONFLICT (tenant_id, provider) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                configured_by = EXCLUDED.configured_by,
                encrypted_payload = EXCLUDED.encrypted_payload,
                status = 'configured',
                last_error = NULL,
                configured_at = now()
            RETURNING id, status
            """
        ),
        {
            "tenant_id": str(ctx.tenant_id),
            "display_name": req.display_name,
            "configured_by": _uc_uid(ctx),
            "encrypted_payload": encrypted,
        },
    )
    data = row.mappings().one()
    return ConfigureConnectorOutput(connector_id=uuid.UUID(str(data["id"])), status=data["status"])


async def get_oauth_start_url(ctx: MCPContext, req: OAuthStartInput) -> OAuthStartOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("connectors:write",))
    state, _verifier, challenge = create_state(req.provider, _uc_uid(ctx), str(ctx.tenant_id))
    settings = get_settings()
    if req.provider == "m365":
        params = {
            "client_id": settings.M365_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": settings.M365_REDIRECT_URI,
            "response_mode": "query",
            "scope": "offline_access User.Read Contacts.Read",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        base = (
            f"https://login.microsoftonline.com/{settings.M365_TENANT_ID}"
            "/oauth2/v2.0/authorize"
        )
    else:
        params = {
            "client_id": settings.GMAIL_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": settings.GMAIL_REDIRECT_URI,
            "scope": "openid email https://www.googleapis.com/auth/contacts.readonly",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        base = "https://accounts.google.com/o/oauth2/v2/auth"
    return OAuthStartOutput(redirect_url=f"{base}?{urlencode(params)}", state=state)


async def pull_connector_now(
    ctx: MCPContext,
    req: PullConnectorNowInput,
) -> PullConnectorNowOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("connectors:write",))
    row = await _load_connector(ctx, req.connector_id)
    connector = _connector_for(row["provider"], decrypt_payload(row["encrypted_payload"]))
    run_id, result = await run_connector_pull(
        ctx=ctx,
        connector_id=req.connector_id,
        connector=connector,
        dry_run=req.dry_run,
        triggered_by="user",
    )
    return PullConnectorNowOutput(connector_run_id=run_id, **result.model_dump())


async def list_connector_runs(
    ctx: MCPContext,
    req: ListConnectorRunsInput,
) -> ListConnectorRunsOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("connectors:read",))
    params: dict[str, Any] = {"tenant_id": str(ctx.tenant_id), "limit": req.limit}
    if req.connector_id is not None:
        params["connector_id"] = str(req.connector_id)
        query = text(
            """
            SELECT r.id, r.connector_id,
                   c.provider, c.display_name,
                   r.started_at, r.completed_at, r.status,
                   r.parsed_count, r.proposed_count, r.deduped_count, r.skipped_count,
                   r.error_message, r.triggered_by
            FROM connector_runs r
            LEFT JOIN connector_configs c ON c.id = r.connector_id
            WHERE r.tenant_id = CAST(:tenant_id AS uuid)
              AND r.connector_id = CAST(:connector_id AS uuid)
            ORDER BY r.started_at DESC
            LIMIT :limit
            """
        )
    else:
        query = text(
            """
            SELECT r.id, r.connector_id,
                   c.provider, c.display_name,
                   r.started_at, r.completed_at, r.status,
                   r.parsed_count, r.proposed_count, r.deduped_count, r.skipped_count,
                   r.error_message, r.triggered_by
            FROM connector_runs r
            LEFT JOIN connector_configs c ON c.id = r.connector_id
            WHERE r.tenant_id = CAST(:tenant_id AS uuid)
            ORDER BY r.started_at DESC
            LIMIT :limit
            """
        )
    rows = await ctx.db.execute(query, params)
    records = [
        ConnectorRunRecord(
                id=uuid.UUID(str(row["id"])),
                connector_id=uuid.UUID(str(row["connector_id"])),
                provider=row["provider"],
                display_name=row["display_name"],
                started_at=row["started_at"].isoformat(),
                completed_at=row["completed_at"].isoformat() if row["completed_at"] else None,
                status=row["status"],
                parsed_count=row["parsed_count"],
                proposed_count=row["proposed_count"],
                deduped_count=row["deduped_count"],
                skipped_count=row["skipped_count"],
                error_message=row["error_message"],
                triggered_by=row["triggered_by"],
            )
        for row in rows.mappings()
    ]
    return ListConnectorRunsOutput(items=records, count=len(records))


async def disconnect_connector(
    ctx: MCPContext,
    req: DisconnectConnectorInput,
) -> DisconnectConnectorOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("connectors:write",))
    empty_payload = encrypt_payload({"disconnected": True, "reason": req.reason})
    row = await ctx.db.execute(
        text(
            """
            UPDATE connector_configs
            SET status = 'disconnected',
                encrypted_payload = :encrypted_payload,
                last_error = :reason
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND id = CAST(:connector_id AS uuid)
            RETURNING id, status
            """
        ),
        {
            "tenant_id": str(ctx.tenant_id),
            "connector_id": str(req.connector_id),
            "encrypted_payload": empty_payload,
            "reason": req.reason,
        },
    )
    data = row.mappings().first()
    if data is None:
        raise ToolError(VALIDATION_FAILED, "connector not found", retryable=False)
    return DisconnectConnectorOutput(connector_id=req.connector_id, status=data["status"])


async def _load_connector(ctx: MCPContext, connector_id: uuid.UUID) -> dict[str, Any]:
    row = await ctx.db.execute(
        text(
            """
            SELECT id, provider, encrypted_payload
            FROM connector_configs
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND id = CAST(:connector_id AS uuid)
              AND status IN ('configured', 'error')
            """
        ),
        {"tenant_id": str(ctx.tenant_id), "connector_id": str(connector_id)},
    )
    data = row.mappings().first()
    if data is None:
        raise ToolError(VALIDATION_FAILED, "configured connector not found", retryable=False)
    return dict(data)


def _connector_for(provider: str, payload: dict[str, Any]) -> Connector:
    if provider == "icloud":
        return ICloudConnector(payload)
    if provider == "m365":
        return M365Connector(payload)
    if provider == "gmail":
        return GmailConnector(payload)
    raise ToolError(VALIDATION_FAILED, f"unsupported connector provider: {provider}")


def _uc_uid(ctx: MCPContext) -> str:
    return str(ctx.claims.get("uc_uid") or ctx.user_id)


_TOOL_SPECS: tuple[
    tuple[
        str,
        type[BaseModel],
        type[BaseModel],
        Callable[..., Awaitable[BaseModel]],
        str,
        tuple[str, ...],
        bool,
    ],
    ...,
] = (
    (
        "list_connectors",
        ListConnectorsInput,
        ListConnectorsOutput,
        list_connectors,
        "CLIENT",
        ("connectors:read",),
        True,
    ),
    (
        "configure_icloud_connector",
        ConfigureICloudConnectorInput,
        ConfigureConnectorOutput,
        configure_icloud_connector,
        "STAFF",
        ("connectors:write",),
        False,
    ),
    (
        "get_oauth_start_url",
        OAuthStartInput,
        OAuthStartOutput,
        get_oauth_start_url,
        "STAFF",
        ("connectors:write",),
        False,
    ),
    (
        "pull_connector_now",
        PullConnectorNowInput,
        PullConnectorNowOutput,
        pull_connector_now,
        "STAFF",
        ("connectors:write",),
        False,
    ),
    (
        "list_connector_runs",
        ListConnectorRunsInput,
        ListConnectorRunsOutput,
        list_connector_runs,
        "CLIENT",
        ("connectors:read",),
        True,
    ),
    (
        "disconnect_connector",
        DisconnectConnectorInput,
        DisconnectConnectorOutput,
        disconnect_connector,
        "STAFF",
        ("connectors:write",),
        False,
    ),
)

for _name, _input, _output, _handler, _role, _scopes, _read_only in _TOOL_SPECS:
    register(
        name=_name,
        description=f"{_name.replace('_', ' ')} for external contact connectors.",
        input_model=_input,
        output_model=_output,
        handler=cast(Any, _handler),
        required_role=_role,
        required_scopes=_scopes,
        annotations={
            "readOnlyHint": bool(_read_only),
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
