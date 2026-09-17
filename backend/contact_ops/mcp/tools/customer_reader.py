"""An owner-approved, read-only Customer-Ops connection to one Contact workspace."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import text

from contact_ops.core.config import get_settings
from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import INSUFFICIENT_ROLE, VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register
from contact_ops.services.brigade_jwt_verifier import verify_brigade_jwt


def customer_reader_subject(claims: dict[str, Any] | None, tenant_id: str) -> str:
    """Only a broker-attested Customer client, never a caller-selected identity."""
    if not claims:
        raise ToolError(VALIDATION_FAILED, "invalid Customer-Ops reader proof")
    act = claims.get("act")
    sub = claims.get("sub")
    if (
        not isinstance(act, dict)
        or act.get("client_id") != "customer-ops"
        or not sub
        or act.get("sub") != sub
        or claims.get("workspace_id") != tenant_id
        or claims.get("scopes") != ["contacts:read"]
        or claims.get("role") is not None
    ):
        raise ToolError(
            VALIDATION_FAILED, "proof must identify the Customer-Ops reader for this workspace"
        )
    try:
        return str(uuid.UUID(sub))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ToolError(VALIDATION_FAILED, "invalid service subject") from exc


class ConnectCustomerReaderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    reader_proof: SecretStr = Field(min_length=20, max_length=16000)


class ConnectCustomerReaderOutput(ToolOutput):
    tenant_id: uuid.UUID
    service_subject: str
    client_id: str = "customer-ops"
    role: str = "viewer"
    created: bool


async def connect_customer_ops_reader(
    ctx: MCPContext, req: ConnectCustomerReaderInput
) -> ConnectCustomerReaderOutput:
    require_scopes(ctx, ["tenant:write", "membership:write"])
    # Only a normal locally issued human session; no PAT, service, or broker principal.
    if ctx.claims.get("iss") != get_settings().KEYCLOAK_ISSUER or ctx.claims.get("brigade_claims"):
        raise ToolError(INSUFFICIENT_ROLE, "a Contact-Ops owner session is required")
    owner = await ctx.db.execute(
        text("""
        SELECT t.id, t.hipaa_mode FROM tenants t JOIN user_tenant_membership m
          ON m.tenant_id=t.id AND m.user_uc_uid=:u
        WHERE t.id=:t AND t.owner_user_id=CAST(:u AS uuid) AND t.deprecated_at IS NULL
          AND m.role='admin' AND m.status='active'
        FOR SHARE OF t, m
    """),
        {"t": str(ctx.tenant_id), "u": str(ctx.user_id)},
    )
    owner_row = owner.mappings().first()
    if owner_row is None:
        raise ToolError(INSUFFICIENT_ROLE, "active workspace owner required")
    if owner_row["hipaa_mode"]:
        raise ToolError(
            VALIDATION_FAILED, "Customer reader connections require a separate HIPAA review"
        )
    claims = verify_brigade_jwt(req.reader_proof.get_secret_value())
    subject = customer_reader_subject(claims, str(ctx.tenant_id))
    if subject == str(ctx.user_id):
        raise ToolError(VALIDATION_FAILED, "service and owner must be different identities")
    await ctx.db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"customer-reader:{ctx.tenant_id}:{subject}"},
    )
    found = await ctx.db.execute(
        text("""SELECT role, status FROM user_tenant_membership
        WHERE tenant_id=:t AND user_uc_uid=:u FOR UPDATE"""),
        {"t": str(ctx.tenant_id), "u": subject},
    )
    existing = found.mappings().first()
    if existing and (existing["role"] != "viewer" or existing["status"] != "active"):
        raise ToolError(
            VALIDATION_FAILED,
            "existing membership cannot be changed or reactivated by this connection",
        )
    if not existing:
        await ctx.db.execute(
            text("""INSERT INTO user_tenant_membership
            (tenant_id,user_uc_uid,role,status,is_default,added_by)
            VALUES (:t,:u,'viewer','active',false,:owner)"""),
            {"t": str(ctx.tenant_id), "u": subject, "owner": str(ctx.user_id)},
        )
        await emit_action_event(
            ctx,
            event_type="workspace_access.grant",
            aggregate_type="tenant",
            aggregate_id=ctx.tenant_id,
            payload_before=None,
            payload_after={
                "user_uc_uid": subject,
                "role": "viewer",
                "status": "active",
                "client_id": "customer-ops",
                "via": "owner_connect_customer_reader",
            },
        )
    return ConnectCustomerReaderOutput(
        tenant_id=ctx.tenant_id, service_subject=subject, created=not bool(existing)
    )


register(
    name="connect_customer_ops_reader",
    description=(
        "Owner-session approval for the broker-attested Customer-Ops reader. "
        "Never grants writes or reactivates revoked access."
    ),
    input_model=ConnectCustomerReaderInput,
    output_model=ConnectCustomerReaderOutput,
    handler=connect_customer_ops_reader,
    required_role="CLIENT",
    required_scopes=("tenant:write", "membership:write"),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="none",
)
