"""Operator-only Brigade JWT verification endpoint."""

from __future__ import annotations

import uuid
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text

from contact_ops.core.database import async_session_maker
from contact_ops.mcp.errors import ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.services.brigade_jwt_verifier import verify_brigade_jwt_with_reason

router = APIRouter(prefix="/api/test", tags=["Test"])


class BrigadeJWTVerifyRequest(BaseModel):
    token: str


@router.post("/brigade-jwt-verify")
async def brigade_jwt_verify_endpoint(
    request: Request,
    payload: BrigadeJWTVerifyRequest,
) -> dict[str, Any]:
    _require_admin_debug(request)
    result = verify_brigade_jwt_with_reason(payload.token)
    if not result.valid or result.claims is None:
        return {"valid": False, "reason": result.reason or "invalid-token"}

    claims = result.claims
    tenant_id = str(claims.get("org_id") or "")
    uc_uid = str(claims.get("sub") or "")
    async with async_session_maker() as db:
        row = await db.execute(
            text(
                """
                SELECT id, slug, display_name, hipaa_mode
                FROM tenants
                WHERE id = CAST(:tenant_id AS uuid)
                  AND deprecated_at IS NULL
                """
            ),
            {"tenant_id": tenant_id},
        )
        tenant = row.mappings().first()

    if tenant is None:
        return {"valid": False, "reason": "tenant-not-found"}

    return {
        "valid": True,
        "claims": claims,
        "matched_user": {"uc_uid": uc_uid},
        "tenant": {
            "id": str(tenant["id"]),
            "slug": str(tenant["slug"]),
            "display_name": str(tenant["display_name"]),
            "hipaa_mode": bool(tenant["hipaa_mode"]),
        },
    }


def _require_admin_debug(request: Request) -> None:
    claims = getattr(request.state, "jwt_claims", None)
    if not isinstance(claims, dict):
        claims = {}
    user_id = str(claims.get("sub") or "")
    tenant_id = _tenant_id(claims)
    ctx = MCPContext(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_chain=cast(dict[str, Any], getattr(request.state, "actor_chain", {"sub": user_id})),
        human_authority=str(getattr(request.state, "human_authority", user_id) or user_id),
        db=cast(Any, None),
        audit_db=cast(Any, None),
        request_id=str(getattr(request.state, "request_id", "") or ""),
        claims=claims,
    )
    try:
        require_role(ctx, "ADMIN")
        require_scopes(ctx, ("admin:debug",))
    except ToolError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.code) from exc


def _tenant_id(claims: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(str(claims.get("tenant_id") or ""))
    except (TypeError, ValueError):
        return uuid.UUID("00000000-0000-0000-0000-000000000000")
