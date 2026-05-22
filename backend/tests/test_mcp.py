"""
MCP server smoke tests.

Verify the JSON-RPC over HTTP endpoint responds correctly to
initialize, tools/list, and tools/call requests.
"""

import pytest
import json
from fastapi.testclient import TestClient

from contact_ops.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_mcp_initialize(client):
    """Verify MCP initialize endpoint responds."""
    payload = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "clientInfo": {"name": "test-client", "version": "0.0.1"},
        },
        "id": "test-1",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400  # No tenant_id header
    body = resp.json()
    assert "error" in body


def test_mcp_initialize_with_tenant(client):
    """Verify MCP initialize returns server info when tenant is provided."""
    import uuid
    tenant_id = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))

    payload = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
        "id": "test-2",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={
            "content-type": "application/json",
            "x-tenant-id": tenant_id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["jsonrpc"] == "2.0"
    assert "result" in body
    assert body["result"]["protocolVersion"] == "2025-03-26"
    assert body["result"]["serverInfo"]["name"] == "contact-ops-mcp"
    assert body["result"]["tools"] == []  # Phase 0: no tools registered


def test_mcp_tools_list(client):
    """Verify tools/list returns empty list in Phase 0."""
    import uuid
    tenant_id = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": "test-3",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={
            "content-type": "application/json",
            "x-tenant-id": tenant_id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["tools"] == []


def test_mcp_tools_call(client):
    """Verify tools/call returns placeholder in Phase 0."""
    import uuid
    tenant_id = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "get_person", "arguments": {"id": "abc123"}},
        "id": "test-4",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={
            "content-type": "application/json",
            "x-tenant-id": tenant_id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body


def test_mcp_unknown_method(client):
    """Verify unknown methods return -32601 error."""
    import uuid
    tenant_id = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))

    payload = {
        "jsonrpc": "2.0",
        "method": "foobar/notreal",
        "id": "test-5",
    }
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={
            "content-type": "application/json",
            "x-tenant-id": tenant_id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32601


def test_mcp_batch_request(client):
    """Verify batch requests are handled."""
    import uuid
    tenant_id = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))

    payload = [
        {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
            "id": "batch-1",
        },
        {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": "batch-2",
        },
    ]
    resp = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={
            "content-type": "application/json",
            "x-tenant-id": tenant_id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2


def test_health_check(client):
    """Verify basic health endpoint."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"


def test_health_live(client):
    """Verify liveness probe endpoint."""
    resp = client.get("/health/live", timeout=5)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "alive"
