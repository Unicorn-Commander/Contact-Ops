"""REST endpoints for a person's contact details.

Contact-Ops is MCP-native — the MCP tool surface is the product and carries the
authoritative RBAC (``require_role`` / ``require_scopes``), workspace isolation
(``load_person`` gates on ``canonical_owner_tenant_id == tenant``), and audit
emission. These routes delegate to the SAME MCP tool handlers (built a
``MCPContext`` from the request's JWT claims, as ``server.py`` does) so RBAC,
workspace scoping, and audit can never drift between the two surfaces.

Workspace scoping is RLS-enforced at the session layer (``app.tenant_id`` GUC)
plus re-gated by ``load_person`` inside every handler, so a caller can only ever
reach a person in their own workspace. The billing entitlement/quota gates that
``server.py`` runs around each tool call are re-run here too (lazy-imported
``check_entitlement`` / ``check_quota`` in ``_run_tool``, keyed to each route's
underlying MCP tool name), so a REST write cannot bypass the plan gates.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from contact_ops.core.config import get_settings
from contact_ops.core.database import (
    async_session_maker,
    audit_session_maker,
    bind_session_context,
)
from contact_ops.mcp.errors import (
    DUPLICATE_RECORD,
    ENTITLEMENT_REQUIRED,
    INSUFFICIENT_ROLE,
    INSUFFICIENT_SCOPE,
    PERSON_NOT_FOUND,
    QUOTA_EXCEEDED,
    STALE_ETAG,
    VALIDATION_FAILED,
    ToolError,
)
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools.addresses import UpdateAddressInput, update_address
from contact_ops.mcp.tools.common import enforce_tool_rbac
from contact_ops.mcp.tools.emails import UpdateEmailInput, update_email
from contact_ops.mcp.tools.employment import UpdateEmploymentInput, update_employment
from contact_ops.mcp.tools.identifiers import (
    AddIdentifierInput,
    RemoveIdentifierInput,
    UpdateIdentifierInput,
    add_identifier,
    remove_identifier,
    update_identifier,
)
from contact_ops.mcp.tools.im_handles import (
    AddImHandleInput,
    RemoveImHandleInput,
    UpdateImHandleInput,
    add_im_handle,
    remove_im_handle,
    update_im_handle,
)
from contact_ops.mcp.tools.phones import UpdatePhoneInput, update_phone
from contact_ops.mcp.tools.urls import (
    AddUrlInput,
    RemoveUrlInput,
    UpdateUrlInput,
    add_url,
    remove_url,
    update_url,
)
from contact_ops.models import (
    Email,
    Identifier,
    IMHandle,
    PersonOrgRole,
    Phone,
    PostalAddress,
    Url,
)

router = APIRouter(prefix="/api/people", tags=["People"])

# Stable MCP ToolError code → HTTP status map. Anything unlisted falls back to
# 400 (a validation-style client error) rather than a misleading 500.
_TOOL_ERROR_STATUS: dict[str, int] = {
    INSUFFICIENT_ROLE: status.HTTP_403_FORBIDDEN,
    INSUFFICIENT_SCOPE: status.HTTP_403_FORBIDDEN,
    PERSON_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    VALIDATION_FAILED: status.HTTP_400_BAD_REQUEST,
    DUPLICATE_RECORD: status.HTTP_409_CONFLICT,
    STALE_ETAG: status.HTTP_409_CONFLICT,
    # Billing gates (P-00075): a plan-tier denial is a paywall (402), an
    # exhausted allowance is a hard block on this op (403) — matching the MCP
    # surface, which surfaces these as ENTITLEMENT_REQUIRED / QUOTA_EXCEEDED.
    ENTITLEMENT_REQUIRED: status.HTTP_402_PAYMENT_REQUIRED,
    QUOTA_EXCEEDED: status.HTTP_403_FORBIDDEN,
}

Handler = Callable[[MCPContext, BaseModel], Awaitable[BaseModel]]


def _raise_tool_error(exc: ToolError) -> None:
    """Translate an MCP ToolError into the matching HTTPException (never returns)."""
    raise HTTPException(
        status_code=_TOOL_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
        detail=exc.message,
    )


def _claims(request: Request) -> dict[str, Any]:
    claims = getattr(request.state, "jwt_claims", None)
    if not isinstance(claims, dict) or not claims.get("tenant_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="tenant context unresolved",
        )
    return claims


async def _run_tool(
    request: Request,
    input_model: type[BaseModel],
    handler: Handler,
    payload: dict[str, Any],
    *,
    tool_name: str,
    owned: tuple[uuid.UUID, type[Any], uuid.UUID] | None = None,
) -> dict[str, Any]:
    """Invoke an MCP tool handler from a REST route with a JWT-built context.

    `tool_name` is the underlying MCP tool's registry name, used to run the same
    billing entitlement/quota gates the MCP surface (`server.py`) runs around
    each tool call, so a REST write cannot bypass the plan gates.
    """
    claims = _claims(request)
    settings = get_settings()
    tenant_id = uuid.UUID(str(claims["tenant_id"]))
    user_id = str(claims.get("sub") or claims.get("uc_uid") or "")
    uc_uid = str(claims.get("uc_uid") or user_id)
    actor_chain = getattr(request.state, "actor_chain", {"sub": user_id})
    if not isinstance(actor_chain, dict):
        actor_chain = {"sub": user_id}
    human_authority = str(
        getattr(request.state, "human_authority", user_id) or user_id
    )
    request_id = str(
        getattr(request.state, "request_id", "")
        or request.headers.get("x-request-id")
        or uuid.uuid4()
    )
    try:
        req = input_model.model_validate(payload)
        async with async_session_maker() as db, audit_session_maker() as audit_db:
            await bind_session_context(db, str(tenant_id), uc_uid, settings)
            await bind_session_context(audit_db, str(tenant_id), uc_uid, settings)
            ctx = MCPContext(
                tenant_id=tenant_id,
                user_id=user_id,
                actor_chain=actor_chain,
                human_authority=human_authority,
                db=db,
                audit_db=audit_db,
                request_id=request_id,
                claims=claims,
            )
            # RBAC FIRST (mirrors server.py): resolve the registry ToolDef and run
            # require_role/require_scopes BEFORE the billing gates, so an
            # unauthorized caller is rejected with 403 without ever probing plan
            # state. The handler re-checks RBAC internally, but that would run
            # AFTER the billing gates — exactly the order this guard exists to
            # prevent (server.py deliberately runs RBAC before entitlement).
            tool = get_tool(tool_name)
            if tool is None:
                raise ToolError(
                    VALIDATION_FAILED, f"unknown tool: {tool_name}", retryable=False
                )
            enforce_tool_rbac(ctx, tool)
            if owned is not None:
                await _assert_owned_by_person(ctx, *owned)
            # Billing gates (P-00075): mirror server.py — entitlement then quota,
            # run AFTER the RBAC guard above and BEFORE the handler (which still
            # re-checks RBAC internally). Lazy import (as server.py does) to avoid
            # the billing → mcp.entitlement → mcp.__init__ → server import cycle.
            # Shadow-first + self-host + free-tool short-circuit all live inside
            # check_entitlement / check_quota, so the common path stays cheap —
            # and every current REST tool is free, so these are no-ops today.
            from contact_ops.billing import check_quota
            from contact_ops.mcp.entitlement import check_entitlement

            entitlement_error = await check_entitlement(
                tool_name=tool_name,
                tenant_id=tenant_id,
                db=db,
                settings=settings,
            )
            if entitlement_error is not None:
                _raise_tool_error(entitlement_error)
            quota_error = await check_quota(tool_name, tenant_id, db, settings)
            if quota_error is not None:
                _raise_tool_error(quota_error)
            result = await handler(ctx, req)
            # Match server.py commit order: audit ledger first, then data.
            await audit_db.commit()
            await db.commit()
            return result.model_dump(mode="json")
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ToolError as exc:
        _raise_tool_error(exc)


async def _assert_owned_by_person(
    ctx: MCPContext, person_id: uuid.UUID, model: type[Any], row_id: uuid.UUID,
) -> None:
    """Check nested resource ownership after RBAC, in the mutation transaction."""
    row = await ctx.db.get(model, row_id)
    if row is None or getattr(row, "person_id", None) != person_id:
        raise HTTPException(status_code=404, detail="resource not found for this person")


# ── IM / social handles ──────────────────────────────────────────────────────


@router.post("/{person_id}/im_handles", status_code=status.HTTP_201_CREATED)
async def add_person_im_handle(
    request: Request, person_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    return await _run_tool(
        request, AddImHandleInput, add_im_handle, {**body, "person_id": person_id},
        tool_name="add_im_handle",
    )


@router.patch("/{person_id}/im_handles/{im_handle_id}")
async def update_person_im_handle(
    request: Request, person_id: uuid.UUID, im_handle_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    return await _run_tool(
        request, UpdateImHandleInput, update_im_handle, {**body, "im_handle_id": im_handle_id},
        tool_name="update_im_handle",
        owned=(person_id, IMHandle, im_handle_id),
    )


@router.delete("/{person_id}/im_handles/{im_handle_id}", status_code=status.HTTP_200_OK)
async def delete_person_im_handle(
    request: Request,
    person_id: uuid.UUID,
    im_handle_id: uuid.UUID,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _run_tool(
        request,
        RemoveImHandleInput,
        remove_im_handle,
        {**(body or {}), "im_handle_id": im_handle_id},
        tool_name="remove_im_handle",
        owned=(person_id, IMHandle, im_handle_id),
    )


# ── Identifiers ──────────────────────────────────────────────────────────────


@router.post("/{person_id}/identifiers", status_code=status.HTTP_201_CREATED)
async def add_person_identifier(
    request: Request, person_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    return await _run_tool(
        request,
        AddIdentifierInput,
        add_identifier,
        {**body, "subject_kind": "person", "subject_id": person_id},
        tool_name="add_identifier",
    )


@router.patch("/{person_id}/identifiers/{identifier_id}")
async def update_person_identifier(
    request: Request, person_id: uuid.UUID, identifier_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    return await _run_tool(
        request,
        UpdateIdentifierInput,
        update_identifier,
        {**body, "identifier_id": identifier_id},
        tool_name="update_identifier",
        owned=(person_id, Identifier, identifier_id),
    )


@router.delete("/{person_id}/identifiers/{identifier_id}", status_code=status.HTTP_200_OK)
async def delete_person_identifier(
    request: Request,
    person_id: uuid.UUID,
    identifier_id: uuid.UUID,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _run_tool(
        request,
        RemoveIdentifierInput,
        remove_identifier,
        {**(body or {}), "identifier_id": identifier_id},
        tool_name="remove_identifier",
        owned=(person_id, Identifier, identifier_id),
    )


# ── URLs ─────────────────────────────────────────────────────────────────────


@router.post("/{person_id}/urls", status_code=status.HTTP_201_CREATED)
async def add_person_url(
    request: Request, person_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    return await _run_tool(
        request, AddUrlInput, add_url, {**body, "person_id": person_id},
        tool_name="add_url",
    )


@router.patch("/{person_id}/urls/{url_id}")
async def update_person_url(
    request: Request, person_id: uuid.UUID, url_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    return await _run_tool(
        request, UpdateUrlInput, update_url, {**body, "url_id": url_id},
        tool_name="update_url",
        owned=(person_id, Url, url_id),
    )


@router.delete("/{person_id}/urls/{url_id}", status_code=status.HTTP_200_OK)
async def delete_person_url(
    request: Request,
    person_id: uuid.UUID,
    url_id: uuid.UUID,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _run_tool(
        request, RemoveUrlInput, remove_url, {**(body or {}), "url_id": url_id},
        tool_name="remove_url",
        owned=(person_id, Url, url_id),
    )


# ── Emails ───────────────────────────────────────────────────────────────────


@router.patch("/{person_id}/emails/{email_id}")
async def update_person_email(
    request: Request, person_id: uuid.UUID, email_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    return await _run_tool(
        request, UpdateEmailInput, update_email, {**body, "email_id": email_id},
        tool_name="update_email",
        owned=(person_id, Email, email_id),
    )


# ── Phones ───────────────────────────────────────────────────────────────────


@router.patch("/{person_id}/phones/{phone_id}")
async def update_person_phone(
    request: Request, person_id: uuid.UUID, phone_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    return await _run_tool(
        request, UpdatePhoneInput, update_phone, {**body, "phone_id": phone_id},
        tool_name="update_phone",
        owned=(person_id, Phone, phone_id),
    )


# ── Addresses ────────────────────────────────────────────────────────────────


@router.patch("/{person_id}/addresses/{address_id}")
async def update_person_address(
    request: Request, person_id: uuid.UUID, address_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    return await _run_tool(
        request, UpdateAddressInput, update_address, {**body, "address_id": address_id},
        tool_name="update_address",
        owned=(person_id, PostalAddress, address_id),
    )


# ── Employment ───────────────────────────────────────────────────────────────


@router.patch("/{person_id}/employment/{employment_id}")
async def update_person_employment(
    request: Request, person_id: uuid.UUID, employment_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    return await _run_tool(
        request, UpdateEmploymentInput, update_employment, {**body, "employment_id": employment_id},
        tool_name="update_employment",
        owned=(person_id, PersonOrgRole, employment_id),
    )
