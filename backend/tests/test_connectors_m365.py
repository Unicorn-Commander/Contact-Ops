from __future__ import annotations

import uuid

from contact_ops.connectors.oauth_state import code_challenge, create_state
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.connectors import OAuthStartInput, get_oauth_start_url


def _ctx() -> MCPContext:
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    return MCPContext(
        tenant_id=tenant_id,
        user_id="user-1",
        actor_chain={"sub": "user-1"},
        human_authority="user-1",
        db=None,  # type: ignore[arg-type]
        audit_db=None,  # type: ignore[arg-type]
        request_id="test",
        claims={
            "uc_uid": "user-1",
            "realm_access": {"roles": ["STAFF"]},
            "scope": "connectors:write connectors:read",
        },
    )


async def test_m365_oauth_start_url_contains_pkce_and_state() -> None:
    from contact_ops.core.config import get_settings

    settings = get_settings()
    settings.M365_CLIENT_ID = "client-id"
    settings.M365_REDIRECT_URI = "https://example.test/m365/callback"
    output = await get_oauth_start_url(_ctx(), OAuthStartInput(provider="m365"))
    assert output.state
    assert "login.microsoftonline.com" in output.redirect_url
    assert "code_challenge_method=S256" in output.redirect_url
    assert "Contacts.Read" in output.redirect_url


def test_pkce_challenge_is_stable() -> None:
    state, verifier, challenge = create_state("m365", "user-1", str(uuid.uuid4()))
    assert state
    assert challenge == code_challenge(verifier)


async def test_m365_ensure_fresh_token_refreshes_and_accepts_rotated_token(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    from contact_ops.connectors.m365 import M365Connector

    async def fake_refresh(refresh_token: str) -> dict[str, object]:
        assert refresh_token == "stored-refresh"
        return {
            "access_token": "fresh-access",
            "refresh_token": "rotated-refresh",  # Microsoft rotates the refresh token
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "scopes": "Contacts.Read",
        }

    monkeypatch.setattr("contact_ops.connectors.m365.refresh_m365_token", fake_refresh)
    connector = M365Connector(
        {
            "access_token": "stale",
            "refresh_token": "stored-refresh",
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        }
    )
    assert await connector.ensure_fresh_token() is True
    assert connector.payload["access_token"] == "fresh-access"
    assert connector.payload["refresh_token"] == "rotated-refresh"  # rotation honoured
