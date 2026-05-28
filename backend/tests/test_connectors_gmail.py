from __future__ import annotations

import uuid

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


async def test_gmail_oauth_start_url_contains_people_scope() -> None:
    from contact_ops.core.config import get_settings

    settings = get_settings()
    settings.GMAIL_CLIENT_ID = "gmail-client"
    settings.GMAIL_REDIRECT_URI = "https://example.test/gmail/callback"
    output = await get_oauth_start_url(_ctx(), OAuthStartInput(provider="gmail"))
    assert output.state
    assert "accounts.google.com" in output.redirect_url
    assert "contacts.readonly" in output.redirect_url
    assert "code_challenge_method=S256" in output.redirect_url
