"""
MCP server smoke tests.

Verify the JSON-RPC over HTTP endpoint responds correctly. These tests
target the public behavior as seen by an MCP client.

Requires STANDALONE_MODE since MCP endpoint needs JWT claims.
Track A has removed /mcp from SKIP_PATHS, so we need valid auth context.
"""

import json

import pytest
from fastapi.testclient import TestClient

from contact_ops.main import app


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


def test_mcp_tools_call_unknown_tool_returns_isError(client):
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
