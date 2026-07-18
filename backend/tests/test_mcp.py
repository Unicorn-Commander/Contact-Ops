"""
MCP server smoke tests.

Verify the JSON-RPC over HTTP endpoint responds correctly. These tests
target the public behavior as seen by an MCP client.

Requires STANDALONE_MODE since MCP endpoint needs JWT claims.
Track A has removed /mcp from SKIP_PATHS, so we need valid auth context.
"""

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from contact_ops.main import app
from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, list_tools
from contact_ops.mcp.server import MCPServerInstance


@pytest.fixture(autouse=True)
def _set_standalone():
    """Force standalone mode so JWT middleware yields test claims."""
    from contact_ops.core.config import get_settings

    settings = get_settings()
    settings.STANDALONE_MODE = True
    yield
    settings.STANDALONE_MODE = False


@pytest.fixture
def client():
    return TestClient(app)


class _FakeSession:
    async def rollback(self) -> None:
        pass

    async def commit(self) -> None:
        pass


@asynccontextmanager
async def _fake_session_maker():
    yield _FakeSession()


async def _fake_bind_session_context(*_args, **_kwargs) -> None:
    pass


def test_mcp_initialize_response_shape(client):
    """initialize response has NO tools field at top level, capabilities.tools is an object."""
    payload = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
        "id": "init-1",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    result = body["result"]
    assert "tools" not in result
    assert "capabilities" in result
    assert isinstance(result["capabilities"]["tools"], dict)
    assert result["serverInfo"]["name"] == "contact-ops-mcp"
    assert result["protocolVersion"] == "2025-11-25"


def test_mcp_tools_call_unknown_tool_returns_is_error(client):
    """tools/call for any tool name returns isError: true in Phase 0."""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "get_person", "arguments": {"id": "abc"}},
        "id": "call-1",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body
    assert body["result"]["isError"] is True


def test_mcp_notification_no_response(client):
    """Request with no 'id' (a notification) must not receive a response body."""
    payload = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 204
    assert resp.content == b""


def test_mcp_unknown_method(client):
    """Unknown MCP methods return -32601 JSON-RPC error."""
    payload = {
        "jsonrpc": "2.0",
        "method": "foobar/notreal",
        "id": "err-1",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32601


def test_mcp_tools_list(client):
    """tools/list returns the Phase 1 Contact-Ops tool surface."""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": "list-1",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    names = {tool["name"] for tool in body["result"]["tools"]}
    assert len(names) >= 16
    assert {"create_person", "get_person", "link_relationship", "bulk_link_relationships"} <= names


def test_mcp_parse_error(client):
    """Malformed JSON returns JSON-RPC parse error."""
    resp = client.post(
        "/mcp",
        data="not valid json at all",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == -32700


def test_mcp_empty_batch(client):
    """Empty batch array returns -32600 error."""
    resp = client.post(
        "/mcp",
        data=json.dumps([]),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32600


def test_mcp_admin_tool_rejects_client_claims_before_handler(client, monkeypatch):
    """A CLIENT-authenticated /mcp tools/call cannot run ADMIN tools."""
    import contact_ops.mcp.server as server

    monkeypatch.setattr(server, "async_session_maker", _fake_session_maker)
    monkeypatch.setattr(server, "audit_session_maker", _fake_session_maker)
    monkeypatch.setattr(server, "bind_session_context", _fake_bind_session_context)

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "list_agents", "arguments": {}},
        "id": "rbac-1",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["isError"] is True
    assert body["result"]["structuredContent"]["code"] == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_mcp_dispatch_sends_every_registered_tool_through_central_gate(monkeypatch):
    """CI guard: no registered tool can reach its handler without central RBAC."""
    import contact_ops.mcp.server as server

    class EmptyInput(BaseModel):
        pass

    class EmptyOutput(BaseModel):
        ok: bool

    real_tools = list_tools()
    fake_tools = {}

    async def handler(_ctx: MCPContext, _payload: BaseModel) -> EmptyOutput:
        raise AssertionError("handler ran before central RBAC")

    for tool in real_tools:
        fake_tools[tool.name] = replace(
            tool,
            input_model=EmptyInput,
            output_model=EmptyOutput,
            handler=handler,
        )

    seen: set[str] = set()

    def deny(ctx: MCPContext, tool) -> None:
        seen.add(tool.name)
        raise ToolError("CENTRAL_RBAC", "central rbac checked", retryable=False)

    monkeypatch.setattr(server, "get_tool", fake_tools.get)
    monkeypatch.setattr(server, "async_session_maker", _fake_session_maker)
    monkeypatch.setattr(server, "audit_session_maker", _fake_session_maker)
    monkeypatch.setattr(server, "bind_session_context", _fake_bind_session_context)
    monkeypatch.setattr(server, "enforce_tool_rbac", deny)

    instance = MCPServerInstance(
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        user_id="client-user",
        actor_chain={"sub": "client-user"},
        human_authority="client-user",
        claims={
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "sub": "client-user",
            "uc_uid": "client-user",
            "realm_access": {"roles": ["CLIENT"]},
            "scope": "",
        },
        request_id="central-rbac-test",
    )

    for tool in real_tools:
        result = await instance.handle_tools_call(tool.name, {})
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "CENTRAL_RBAC"

    assert seen == {tool.name for tool in real_tools}
