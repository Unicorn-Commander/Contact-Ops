from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from contact_ops.connectors.base import Connector, ConnectorRunResult, run_connector_pull
from contact_ops.importers.base import CanonicalImportRecord
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.connectors import OAuthStartInput, get_oauth_start_url


def _expiry_iso(delta_seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=delta_seconds)).isoformat()


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


def test_token_needs_refresh_handles_expiry_skew_and_bad_input() -> None:
    from contact_ops.connectors.base import token_needs_refresh

    assert token_needs_refresh({}) is True  # no expiry recorded
    assert token_needs_refresh({"expires_at": "not-a-date"}) is True
    assert token_needs_refresh({"expires_at": _expiry_iso(-60)}) is True  # expired
    assert token_needs_refresh({"expires_at": _expiry_iso(120)}) is True  # inside 5-min skew
    assert token_needs_refresh({"expires_at": _expiry_iso(3600)}) is False  # comfortably valid
    # A naive timestamp is interpreted as UTC rather than raising.
    naive = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert token_needs_refresh({"expires_at": naive}) is False


def test_apply_refreshed_token_preserves_refresh_token_and_scopes_when_omitted() -> None:
    from contact_ops.connectors.base import apply_refreshed_token

    payload = {
        "access_token": "old",
        "refresh_token": "stored-refresh",
        "expires_at": _expiry_iso(-1),
        "scopes": "openid contacts.readonly",
    }
    # Google omits the refresh token (and sometimes the scope) on a refresh response.
    apply_refreshed_token(
        payload,
        {
            "access_token": "fresh",
            "expires_at": _expiry_iso(3600),
            "refresh_token": None,
            "scopes": "",
        },
    )
    assert payload["access_token"] == "fresh"
    assert payload["refresh_token"] == "stored-refresh"
    assert payload["scopes"] == "openid contacts.readonly"


async def test_gmail_ensure_fresh_token_refreshes_expired_token(monkeypatch) -> None:
    from contact_ops.connectors.gmail import GmailConnector

    seen: list[str] = []

    async def fake_refresh(refresh_token: str) -> dict[str, object]:
        seen.append(refresh_token)
        return {
            "access_token": "fresh-access",
            "refresh_token": None,  # Google does not return a new one
            "expires_at": _expiry_iso(3600),
            "scopes": "",
        }

    monkeypatch.setattr("contact_ops.connectors.gmail.refresh_gmail_token", fake_refresh)
    connector = GmailConnector(
        {
            "access_token": "stale",
            "refresh_token": "stored-refresh",
            "expires_at": _expiry_iso(-60),
            "scopes": "openid https://www.googleapis.com/auth/contacts.readonly",
        }
    )
    changed = await connector.ensure_fresh_token()
    assert changed is True
    assert seen == ["stored-refresh"]
    assert connector.payload["access_token"] == "fresh-access"
    assert connector.payload["refresh_token"] == "stored-refresh"  # preserved
    assert "contacts.readonly" in connector.payload["scopes"]  # preserved


async def test_gmail_ensure_fresh_token_noop_when_valid(monkeypatch) -> None:
    from contact_ops.connectors.gmail import GmailConnector

    async def must_not_call(refresh_token: str) -> dict[str, object]:
        raise AssertionError("refresh must not run while the token is valid")

    monkeypatch.setattr("contact_ops.connectors.gmail.refresh_gmail_token", must_not_call)
    connector = GmailConnector(
        {"access_token": "good", "refresh_token": "r", "expires_at": _expiry_iso(3600)}
    )
    assert await connector.ensure_fresh_token() is False
    assert connector.payload["access_token"] == "good"


async def test_gmail_ensure_fresh_token_noop_without_refresh_token() -> None:
    from contact_ops.connectors.gmail import GmailConnector

    connector = GmailConnector({"access_token": "stale", "expires_at": _expiry_iso(-60)})
    assert await connector.ensure_fresh_token() is False


class _RetryConnector(Connector):
    provider = "gmail"

    def __init__(self) -> None:
        super().__init__({"access_token": "stale", "refresh_token": "refresh"})
        self.pull_calls = 0
        self.refresh_calls = 0

    async def refresh_access_token(self) -> bool:
        self.refresh_calls += 1
        self.payload["access_token"] = "fresh"
        self.payload["expires_at"] = _expiry_iso(3600)
        return True

    async def pull(self, ctx: MCPContext, since: datetime | None) -> list[CanonicalImportRecord]:
        self.pull_calls += 1
        if self.pull_calls == 1:
            request = httpx.Request("GET", "https://people.googleapis.test")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("unauthorized", request=request, response=response)
        return [CanonicalImportRecord(source_record_id="1", display_name="Ada Lovelace")]


class _FakeDb:
    async def execute(self, *_args, **_kwargs):
        class Result:
            @staticmethod
            def scalar_one() -> uuid.UUID:
                return uuid.UUID("00000000-0000-0000-0000-000000000099")

        return Result()


@pytest.mark.asyncio
async def test_run_connector_pull_refreshes_persists_and_retries_after_401(monkeypatch) -> None:
    persisted: list[dict[str, object]] = []

    async def fake_persist(_tenant_id, _connector_id, payload):
        persisted.append(dict(payload))

    monkeypatch.setattr("contact_ops.connectors.base._persist_connector_payload", fake_persist)
    monkeypatch.setattr("contact_ops.connectors.base._run_post_pull_agent_pipeline", lambda *_: None)

    connector = _RetryConnector()
    ctx = _ctx()
    ctx.db = _FakeDb()  # type: ignore[assignment]
    run_id, result = await run_connector_pull(
        ctx=ctx,
        connector_id=uuid.UUID("00000000-0000-0000-0000-000000000010"),
        connector=connector,
        dry_run=True,
    )

    assert run_id == uuid.UUID("00000000-0000-0000-0000-000000000099")
    assert isinstance(result, ConnectorRunResult)
    assert result.parsed_count == 1
    assert connector.pull_calls == 2
    assert connector.refresh_calls == 1
    assert persisted[-1]["access_token"] == "fresh"
