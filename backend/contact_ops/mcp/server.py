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
from pydantic import BaseModel, Field, ValidationError

logger = structlog.get_logger(__name__)

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
    name: str
    description: str
    inputSchema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})


# Capabilities are objects (or null), never bool. Phase 0 advertises only the
# `tools` capability so MCP clients know they can call `tools/list`.
CAPABILITIES: dict[str, Any] = {"tools": {"listChanged": False}}


class MCPServerInstance:
    """Per-request server bound to a single tenant.

    State lives only for the duration of the request. Each MCP method receives
    the instance so it can read tenant context without touching args.
    """

    def __init__(self, tenant_id: uuid.UUID, user_id: str | None = None) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self._tools: list[MCPTool] = []

    def register_tool(self, tool: MCPTool) -> None:
        self._tools.append(tool)

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
        return {"tools": [t.model_dump() for t in self._tools]}

    def handle_tools_call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        # Phase 0: no tools are registered. Every call returns a tool-level
        # error via the `isError` flag (per the MCP spec — application errors
        # for tool execution belong here, not in the JSON-RPC error envelope).
        tool_exists = any(t.name == name for t in self._tools)
        if not tool_exists:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"No tool named '{name}' is registered. "
                            "Phase 0 of Contact-Ops ships the MCP scaffold only; "
                            "tools land in Phase 1."
                        ),
                    }
                ],
                "isError": True,
            }
        # Phase 1+ tool dispatch will live here.
        return {"content": [{"type": "text", "text": "not reachable in Phase 0"}], "isError": True}


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
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid tenant_id claim")
    user_id = claims.get("sub")

    server = MCPServerInstance(tenant_id=tenant_id, user_id=user_id)

    # Batch vs single
    if isinstance(body, list):
        if not body:
            # Per JSON-RPC 2.0 spec, empty array → InvalidRequest
            return _ok(_jsonrpc_error(None, -32600, "Invalid Request: empty batch"))
        responses = [_dispatch_one(server, item) for item in body]
        # Filter out notification responses (None)
        responses = [r for r in responses if r is not None]
        if not responses:
            # All notifications — no response body
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return _ok(responses)

    response = _dispatch_one(server, body)
    if response is None:
        # Notification — spec says do not respond
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _ok(response)


def _dispatch_one(server: MCPServerInstance, raw: Any) -> dict[str, Any] | None:
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
        result = _route(server, req)
    except _MCPMethodNotFound:
        if is_notification:
            return None
        return _jsonrpc_error(req.id, -32601, f"Method not found: {req.method}")
    except _MCPInvalidParams as exc:
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


def _route(server: MCPServerInstance, req: JSONRPCRequest) -> Any:
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
            raise _MCPInvalidParams("tools/call requires `name` (non-empty string)")
        arguments = params.get("arguments")
        if arguments is not None and not isinstance(arguments, dict):
            raise _MCPInvalidParams("tools/call `arguments` must be an object if provided")
        return server.handle_tools_call(name=name, arguments=arguments)

    raise _MCPMethodNotFound(method)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MCPMethodNotFound(Exception):
    pass


class _MCPInvalidParams(Exception):
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
