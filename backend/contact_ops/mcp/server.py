"""MCP server for Contact-Ops.

JSON-RPC 2.0 over HTTP, per the Model Context Protocol spec (2025-11-25).
Per-request server instance bound to the calling user's tenant. Tenant is
resolved from the JWT claim (`request.state.jwt_claims["tenant_id"]`), NEVER
from request headers — the JWT middleware is the only trusted source.

Phase 0: scaffold only — no tools registered. `tools/list` returns an empty
list; `tools/call` returns an MCP `isError: true` response. Phase 1 lands the
first ~30 tools per design doc §5.

Spec compliance notes:
- `initialize` response: `{ protocolVersion, capabilities, serverInfo }` only —
  NO `tools` field at top level (that's only in `tools/list` responses).
- `capabilities.tools` is an object (`{}` or `{listChanged: bool}`), not a bool.
- `tools/call` for an unknown tool returns `{ content, isError: true }` per
  the spec, not a JSON-RPC error envelope.
- JSON-RPC application errors return HTTP 200 with an `error` envelope in the
  body, NOT HTTP 4xx (HTTP status is for transport-layer failures only).
- Notifications (requests with no `id`) MUST NOT receive a response.
- Empty batch (`[]`) returns a JSON-RPC -32600 error.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contact_ops.core.config import get_settings
from contact_ops.core.database import (
    async_session_maker,
    audit_session_maker,
    bind_session_context,
)
from contact_ops.mcp.entitlement import DEFAULT_TIER, check_entitlement, tier_for_tool
from contact_ops.mcp.errors import ToolError, error_result
from contact_ops.mcp.registry import MCPContext, get_tool, list_tools
from contact_ops.mcp.tools import register_all_tools

logger = structlog.get_logger(__name__)
register_all_tools()

router = APIRouter(tags=["MCP"])

SERVER_NAME = "contact-ops-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-11-25"


# ---------------------------------------------------------------------------
# JSON-RPC + MCP types
# ---------------------------------------------------------------------------


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: dict[str, Any] | None = None
    id: str | int | None = None


class MCPTool(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object"},
        alias="inputSchema",
    )
    annotations: dict[str, bool] = Field(default_factory=dict)


# Capabilities are objects (or null), never bool. Phase 0 advertises only the
# `tools` capability so MCP clients know they can call `tools/list`.
CAPABILITIES: dict[str, Any] = {"tools": {"listChanged": False}}


class MCPServerInstance:
    """Per-request server bound to a single tenant.

    State lives only for the duration of the request. Each MCP method receives
    the instance so it can read tenant context without touching args.
    """

    def __init__(
        self,
        tenant_id: uuid.UUID,
        user_id: str,
        actor_chain: dict[str, Any],
        human_authority: str,
        claims: dict[str, Any],
        request_id: str,
    ) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.actor_chain = actor_chain
        self.human_authority = human_authority
        self.claims = claims
        self.request_id = request_id

    def handle_initialize(
        self, protocol_version: str, client_info: dict[str, Any] | None
    ) -> dict[str, Any]:
        # The spec allows the server to negotiate down; for Phase 0 we just
        # echo the client's version if we recognize it, otherwise return ours.
        # Any future incompatibility lands here.
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": CAPABILITIES,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def handle_tools_list(self) -> dict[str, Any]:
        tools = [
            MCPTool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.input_model.model_json_schema(),
                annotations=tool.annotations,
            ).model_dump(by_alias=True)
            for tool in list_tools()
        ]
        return {"tools": tools}

    async def handle_tools_call(
        self,
        name: str,
        arguments: dict[str, Any] | None,
    ) -> dict[str, Any]:
        tool = get_tool(name)
        if tool is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"No tool named '{name}' is registered.",
                    }
                ],
                "isError": True,
            }
        try:
            parsed = tool.input_model.model_validate(arguments or {})
        except ValidationError as exc:
            return error_result(ToolError("VALIDATION_FAILED", str(exc), retryable=False))

        settings = get_settings()
        # Lazy import: contact_ops.billing pulls contact_ops.mcp.entitlement, which
        # triggers contact_ops.mcp.__init__ -> server (this module). Importing
        # billing at module scope would be a circular import; importing it here (by
        # which point everything is fully loaded) breaks the cycle.
        from contact_ops.billing import (
            AGENT_RUN_METRIC,
            check_quota,
            get_billing_provider,
        )

        # uc_uid source: self.user_id (set from claims["sub"]/uc_uid at
        # mcp_endpoint); self.claims["uc_uid"] is normalized + guaranteed.
        uc_uid = self.claims.get("uc_uid") or self.user_id
        async with async_session_maker() as db, audit_session_maker() as audit_db:
            await bind_session_context(db, str(self.tenant_id), uc_uid, settings)
            await bind_session_context(audit_db, str(self.tenant_id), uc_uid, settings)
            # Membership gate (design §4.4): a human Keycloak session must hold an
            # active user_tenant_membership for its tenant. Flag-gated (off until
            # memberships are seeded), Keycloak-issuer-scoped (PAT/Brigade/standalone
            # use their own validation paths). Fail-closed.
            if (
                settings.MEMBERSHIP_GATE_ENFORCED
                and self.claims.get("iss") == settings.KEYCLOAK_ISSUER
            ):
                from contact_ops.core.database import _user_is_active_member

                if not await _user_is_active_member(db, uc_uid, str(self.tenant_id)):
                    await db.rollback()
                    await audit_db.rollback()
                    return error_result(
                        ToolError(
                            "NOT_A_MEMBER",
                            "not a member of this workspace",
                            retryable=False,
                        )
                    )
            # Entitlement gate (P-00075 §4): per-tool plan-tier enforcement.
            # Runs AFTER the membership gate (so the GUC is bound for the plan
            # read) and BEFORE the handler — free tools short-circuit with no DB
            # I/O. Shadow-first + self-host bypass live inside check_entitlement.
            entitlement_error = await check_entitlement(
                tool_name=tool.name,
                tenant_id=self.tenant_id,
                db=db,
                settings=settings,
            )
            if entitlement_error is not None:
                await db.rollback()
                await audit_db.rollback()
                return error_result(entitlement_error)
            # Quota gate (P-00075 billing): plan ALLOWANCE check layered on the
            # entitlement gate (same RLS-bound db). Shadow-first + self-host
            # bypass live inside check_quota; free tools short-circuit, no DB I/O.
            quota_error = await check_quota(tool.name, self.tenant_id, db, settings)
            if quota_error is not None:
                await db.rollback()
                await audit_db.rollback()
                return error_result(quota_error)
            ctx = MCPContext(
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                actor_chain=self.actor_chain,
                human_authority=self.human_authority,
                db=db,
                audit_db=audit_db,
                request_id=self.request_id,
                claims=self.claims,
            )
            try:
                output = await tool.handler(ctx, parsed)
                # Commit the audit/Undo ledger (audit_db) BEFORE the data (db). Both
                # point at the same Postgres, so the gap is tiny, but if a crash hits
                # between the two commits this fails in the SAFE direction: an orphan
                # ledger row (a reversible op whose data didn't land) is harmless —
                # unmerge_people/_organizations just no-op on already-canonical rows —
                # whereas committing data first would strand UNRECOVERABLE changes with
                # no Undo record (the action_event is the sole reversibility ledger).
                await audit_db.commit()
                await db.commit()
                # Meter the op AFTER it's durably committed (P-00075 billing).
                # Best-effort + never raises; only metered (pro/enterprise) tools
                # emit, self-host is never metered. Dormant: the Manual provider
                # just logs billing_would_emit (zero external calls).
                if (
                    settings.DEPLOYMENT_MODE != "self_host"
                    and tier_for_tool(tool.name) != DEFAULT_TIER
                ):
                    await get_billing_provider(settings).emit_usage(
                        tenant_id=str(self.tenant_id),
                        code=AGENT_RUN_METRIC,
                        properties={"tool": tool.name},
                    )
            except ToolError as exc:
                await db.rollback()
                await audit_db.rollback()
                return error_result(exc)
            except Exception:
                await db.rollback()
                await audit_db.rollback()
                raise
        structured = output.model_dump(mode="json")
        return {
            "content": [{"type": "text", "text": json.dumps(structured, sort_keys=True)}],
            "structuredContent": structured,
            "isError": False,
        }


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    """JSON-RPC 2.0 endpoint.

    Tenant comes from the validated JWT claims (placed on request.state by
    JWTValidationMiddleware). No header trust.
    """
    claims = getattr(request.state, "jwt_claims", None)
    if not claims or not claims.get("tenant_id"):
        # JWT middleware should have already 401'd; defensive 401 here.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthenticated")

    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        return _http_error(
            status.HTTP_400_BAD_REQUEST,
            _jsonrpc_error(None, -32700, f"Parse error: {exc}"),
        )

    try:
        tenant_id = uuid.UUID(str(claims["tenant_id"]))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid tenant_id claim",
        ) from exc
    user_id = str(claims.get("sub") or "")
    server = MCPServerInstance(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_chain=getattr(request.state, "actor_chain", {"sub": user_id}),
        human_authority=str(getattr(request.state, "human_authority", user_id) or user_id),
        claims=claims,
        request_id=str(
            getattr(request.state, "request_id", "")
            or request.headers.get("x-request-id")
            or uuid.uuid4()
        ),
    )

    # Batch vs single
    if isinstance(body, list):
        if not body:
            # Per JSON-RPC 2.0 spec, empty array → InvalidRequest
            return _ok(_jsonrpc_error(None, -32600, "Invalid Request: empty batch"))
        responses = [await _dispatch_one(server, item) for item in body]
        # Filter out notification responses (None)
        responses = [r for r in responses if r is not None]
        if not responses:
            # All notifications — no response body
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return _ok(responses)

    response = await _dispatch_one(server, body)
    if response is None:
        # Notification — spec says do not respond
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _ok(response)


async def _dispatch_one(server: MCPServerInstance, raw: Any) -> dict[str, Any] | None:
    """Dispatch a single JSON-RPC request. Returns None for notifications."""
    if not isinstance(raw, dict):
        return _jsonrpc_error(None, -32600, "Invalid Request: not an object")

    try:
        req = JSONRPCRequest.model_validate(raw)
    except ValidationError as exc:
        request_id = raw.get("id") if isinstance(raw, dict) else None
        return _jsonrpc_error(request_id, -32600, f"Invalid Request: {exc}")

    if req.jsonrpc != "2.0":
        return _jsonrpc_error(req.id, -32600, "Invalid Request: jsonrpc must be '2.0'")

    is_notification = req.id is None and req.method.startswith("notifications/")

    try:
        result = await _route(server, req)
    except _MCPMethodNotFoundError:
        if is_notification:
            return None
        return _jsonrpc_error(req.id, -32601, f"Method not found: {req.method}")
    except _MCPInvalidParamsError as exc:
        if is_notification:
            return None
        return _jsonrpc_error(req.id, -32602, str(exc))
    except Exception as exc:
        logger.error("mcp_internal_error", method=req.method, error=str(exc), exc_info=True)
        if is_notification:
            return None
        return _jsonrpc_error(req.id, -32603, "Internal error")

    if is_notification or req.id is None:
        # Notifications get no response, even on success.
        return None

    return {"jsonrpc": "2.0", "result": result, "id": req.id}


async def _route(server: MCPServerInstance, req: JSONRPCRequest) -> Any:
    method = req.method
    params = req.params or {}

    if method == "initialize":
        return server.handle_initialize(
            protocol_version=params.get("protocolVersion", PROTOCOL_VERSION),
            client_info=params.get("clientInfo"),
        )

    if method == "notifications/initialized":
        # Spec: server MUST NOT respond. We ack via the caller path.
        return None

    if method == "tools/list":
        return server.handle_tools_list()

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise _MCPInvalidParamsError("tools/call requires `name` (non-empty string)")
        arguments = params.get("arguments")
        if arguments is not None and not isinstance(arguments, dict):
            raise _MCPInvalidParamsError(
                "tools/call `arguments` must be an object if provided"
            )
        return await server.handle_tools_call(name=name, arguments=arguments)

    raise _MCPMethodNotFoundError(method)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MCPMethodNotFoundError(Exception):
    pass


class _MCPInvalidParamsError(Exception):
    pass


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": request_id}


def _ok(body: Any) -> Response:
    return Response(
        content=json.dumps(body),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


def _http_error(http_status: int, body: dict[str, Any]) -> Response:
    return Response(
        content=json.dumps(body),
        status_code=http_status,
        media_type="application/json",
    )
