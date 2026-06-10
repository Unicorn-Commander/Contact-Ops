"""REST endpoint for propose-only CSV imports (Google Contacts / LinkedIn)."""

from __future__ import annotations

import uuid
from typing import Any, cast

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from contact_ops.core.config import get_settings
from contact_ops.core.database import (
    async_session_maker,
    audit_session_maker,
    bind_session_context,
)
from contact_ops.mcp.registry import MCPContext
from contact_ops.services.csv_import import (
    MAX_CSV_SIZE_BYTES,
    CSVImportError,
    decode_csv_bytes,
    propose_csv_import,
)
from contact_ops.services.import_propose import ImportSummary

router = APIRouter(tags=["Import"])
CSV_FILE_FIELD = File(...)
DRY_RUN_FIELD = Form(default="false")
AUTO_APPROVE_FIELD = Form(default="false")


@router.post("/import/csv")
async def import_csv_endpoint(
    request: Request,
    file: UploadFile = CSV_FILE_FIELD,
    dry_run: str = DRY_RUN_FIELD,
    auto_approve: str = AUTO_APPROVE_FIELD,
) -> ImportSummary:
    ctx_base = _context_from_request(request)
    body = await _read_limited(file)
    try:
        text_body = decode_csv_bytes(body)
    except CSVImportError as exc:
        if "exceeds" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=str(exc),
            ) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    settings = get_settings()
    # uc_uid source: ctx_base.claims["uc_uid"] (normalized at JWT validation),
    # falling back to ctx_base.user_id (== claims["sub"]).
    uc_uid = str(ctx_base.claims.get("uc_uid") or ctx_base.user_id)
    async with async_session_maker() as db, audit_session_maker() as audit_db:
        await bind_session_context(db, str(ctx_base.tenant_id), uc_uid, settings)
        await bind_session_context(audit_db, str(ctx_base.tenant_id), uc_uid, settings)
        ctx_base.db = db
        ctx_base.audit_db = audit_db
        try:
            result = await propose_csv_import(
                ctx=ctx_base,
                csv_text=text_body,
                dry_run=_parse_bool(dry_run),
                auto_approve=_parse_bool(auto_approve),
                filename=file.filename,
            )
            await db.commit()
            await audit_db.commit()
        except CSVImportError as exc:
            await db.rollback()
            await audit_db.rollback()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except Exception:
            await db.rollback()
            await audit_db.rollback()
            raise
    return result


async def _read_limited(file: UploadFile) -> bytes:
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > MAX_CSV_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="CSV file exceeds 50 MiB",
            )
    return bytes(data)


def _context_from_request(request: Request) -> MCPContext:
    claims = getattr(request.state, "jwt_claims", None)
    if not isinstance(claims, dict) or not claims.get("tenant_id"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthenticated")
    try:
        tenant_id = uuid.UUID(str(claims["tenant_id"]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid tenant_id claim",
        ) from exc
    user_id = str(claims.get("sub") or "")
    return MCPContext(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_chain=_state_dict(request, "actor_chain", {"sub": user_id}),
        human_authority=str(getattr(request.state, "human_authority", user_id) or user_id),
        db=cast(Any, None),
        audit_db=cast(Any, None),
        request_id=str(
            getattr(request.state, "request_id", "")
            or request.headers.get("x-request-id")
            or uuid.uuid4()
        ),
        claims=claims,
    )


def _state_dict(request: Request, key: str, default: dict[str, Any]) -> dict[str, Any]:
    value = getattr(request.state, key, default)
    if isinstance(value, dict):
        return value
    return default


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
