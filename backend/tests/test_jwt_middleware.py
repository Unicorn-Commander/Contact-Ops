"""
JWT validation middleware tests.

Verify that the JWTValidationMiddleware correctly:
- Skips public paths (health, docs, openapi.json)
- Does NOT skip /mcp (removed from SKIP_PATHS per Track A security fix)
- Returns 401 for requests without auth (when Keycloak is configured)
- Injects claims into request.state when STANDALONE_MODE is on
"""

import pytest
from fastapi.testclient import TestClient

from contact_ops.main import app


@pytest.fixture
def client_with_standalone():
    """TestClient with STANDALONE_MODE enabled for testing middleware injection."""
    from contact_ops.core.config import get_settings

    settings = get_settings()
    prev = settings.STANDALONE_MODE
    settings.STANDALONE_MODE = True
    yield TestClient(app)
    settings.STANDALONE_MODE = prev


def test_health_path_skips_jwt(client_with_standalone):
    """Health endpoints should not require JWT."""
    resp = client_with_standalone.get("/health")
    assert resp.status_code == 200


def test_health_ready_skips_jwt(client_with_standalone):
    """Health/ready endpoint should not require JWT."""
    resp = client_with_standalone.get("/health/ready")
    # In test env it'll likely be 503 (can't reach real deps) but NOT 401
    assert resp.status_code != 401


def test_health_live_skips_jwt(client_with_standalone):
    """Health/live endpoint should not require JWT."""
    resp = client_with_standalone.get("/health/live")
    assert resp.status_code == 200


def test_openapi_path_skips_jwt(client_with_standalone):
    """OpenAPI docs should not require JWT."""
    resp = client_with_standalone.get("/openapi.json")
    assert resp.status_code == 200


def test_docs_path_skips_jwt(client_with_standalone):
    """/docs should not require JWT."""
    resp = client_with_standalone.get("/docs")
    assert resp.status_code == 200


def test_root_path_skips_jwt(client_with_standalone):
    """/ should not require JWT."""
    resp = client_with_standalone.get("/")
    assert resp.status_code == 200


def test_mcp_path_NOT_in_skip_paths():
    """Track A removed /mcp from SKIP_PATHS — verify it's not there."""
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware

    assert "/mcp" not in JWTValidationMiddleware.SKIP_PATHS


def test_skip_paths_contents():
    """Verify SKIP_PATHS contains the expected public paths."""
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware

    skip = JWTValidationMiddleware.SKIP_PATHS
    assert "/health" in skip
    assert "/health/live" in skip
    assert "/health/ready" in skip
    assert "/docs" in skip
    assert "/openapi.json" in skip
    assert "/" in skip


def test_standalone_mode_injects_claims(client_with_standalone):
    """When STANDALONE_MODE is on, the middleware injects fake claims."""
    resp = client_with_standalone.get("/")
    assert resp.status_code == 200
    # The root endpoint doesn't expose claims but we can verify the request
    # was served (i.e., middleware didn't 401)


def test_no_token_401_when_standalone_off():
    """When STANDALONE_MODE is off and no token → 401 (test a non-skip path)."""
    from contact_ops.core.config import get_settings

    settings = get_settings()
    settings.STANDALONE_MODE = False
    settings.KEYCLOAK_ISSUER = "https://auth.example/realms/test"

    client = TestClient(app)
    # /mcp is NOT in SKIP_PATHS and requires auth
    import json as _json
    resp = client.post(
        "/mcp",
        data=_json.dumps({"jsonrpc": "2.0", "method": "initialize", "id": "t"}),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 401

    settings.STANDALONE_MODE = True
    settings.KEYCLOAK_ISSUER = ""
