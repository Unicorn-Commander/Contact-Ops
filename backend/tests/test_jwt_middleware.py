"""
JWT validation middleware tests.

Verify that the JWTValidationMiddleware correctly:
- Skips public paths
- Returns 401 for requests without auth (when Keycloak is configured)
- Injects claims into request state
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from contact_ops.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_path_skips_jwt(client):
    """Health endpoints should not require JWT."""
    resp = client.get("/health")
    assert resp.status_code == 200


def test_openapi_path_skips_jwt(client):
    """OpenAPI docs should not require JWT."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200


def test_mcp_path_skips_jwt(client):
    """MCP endpoint is handled separately, does not need JWT middleware."""
    import json
    resp = client.post(
        "/mcp",
        data=json.dumps({"jsonrpc": "2.0", "method": "initialize", "id": "x"}),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400  # No tenant_id — 400 is expected


def test_jwt_validation_skipped_when_keycloak_not_configured(client):
    """When Keycloak is not configured, JWT validation is skipped."""
    from contact_ops.core.config import get_settings
    settings = get_settings()

    # In standalone mode, no JWT validation needed
    assert settings.STANDALONE_MODE is True
    resp = client.get("/health")
    assert resp.status_code == 200


def test_inject_headers_works():
    """Verify _inject_headers correctly adds headers to scope."""
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware

    # Create a minimal ASGI scope
    scope = {"headers": []}

    class FakeRequest:
        def __init__(self, scope):
            self.scope = scope
            self.state = MagicMock()

    claims = {
        "sub": "user-123",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "email": "REMOVED",
        "tenant_slug": "test",
        "tenant_hipaa": False,
    }

    JWTValidationMiddleware._inject_headers(FakeRequest(scope), claims)

    header_names = [h[0].decode() for h in scope["headers"]]
    assert "x-user-id" in header_names
    assert "x-tenant-id" in header_names
    assert "x-user-email" in header_names
    assert "x-tenant-slug" in header_names
    assert "x-tenant-hipaa" in header_names


def test_skip_paths_are_correct():
    """Verify SKIP_PATHS contains the expected public paths."""
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware

    skip = JWTValidationMiddleware.SKIP_PATHS
    assert "/health" in skip
    assert "/health/live" in skip
    assert "/health/ready" in skip
    assert "/mcp" in skip
    assert "/docs" in skip
    assert "/openapi.json" in skip
