from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from contact_ops.core.config import Settings
from contact_ops.core.database import get_tenant_db
from contact_ops.mcp.server import MCPServerInstance

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _settings() -> Settings:
    return Settings(
        ENV="test",
        DATABASE_URL="postgresql+asyncpg://x:y@localhost:5432/z",
        KEYCLOAK_ISSUER="https://auth.example.test/realms/uchub",
        STANDALONE_MODE=False,
        MEMBERSHIP_GATE_ENFORCED=True,
    )


class _FakeSession:
    async def rollback(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def execute(self, *_args, **_kwargs):
        return None


@asynccontextmanager
async def _fake_session_maker():
    yield _FakeSession()


@pytest.mark.asyncio
async def test_non_member_rejected_by_get_tenant_db(monkeypatch) -> None:
    import contact_ops.core.database as database

    async def not_member(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(database, "async_session_maker", _fake_session_maker)
    monkeypatch.setattr(database, "_user_is_active_member", not_member)
    request = SimpleNamespace(
        state=SimpleNamespace(
            jwt_claims={
                "iss": _settings().KEYCLOAK_ISSUER,
                "tenant_id": str(TENANT_ID),
                "sub": "non-member",
                "uc_uid": "non-member",
            }
        )
    )

    dep = get_tenant_db(request, settings=_settings())
    with pytest.raises(HTTPException) as exc:
        await dep.__anext__()
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_member_rejected_by_mcp_server(monkeypatch) -> None:
    import contact_ops.mcp.server as server

    async def not_member(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(server, "get_settings", _settings)
    monkeypatch.setattr(server, "async_session_maker", _fake_session_maker)
    monkeypatch.setattr(server, "audit_session_maker", _fake_session_maker)
    monkeypatch.setattr("contact_ops.core.database._user_is_active_member", not_member)

    instance = MCPServerInstance(
        tenant_id=TENANT_ID,
        user_id="non-member",
        actor_chain={"sub": "non-member"},
        human_authority="non-member",
        claims={
            "iss": _settings().KEYCLOAK_ISSUER,
            "tenant_id": str(TENANT_ID),
            "sub": "non-member",
            "uc_uid": "non-member",
            "realm_access": {"roles": ["CLIENT"]},
            "scope": "tenant:read",
        },
        request_id="test",
    )
    result = await instance.handle_tools_call("list_tenants", {})
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "NOT_A_MEMBER"


@pytest.mark.asyncio
async def test_non_member_brigade_token_rejected_by_jwt_middleware(monkeypatch) -> None:
    import contact_ops.middleware.jwt_validation as jwt_validation

    settings = _settings()

    async def not_member(*_args, **_kwargs) -> bool:
        return False

    class _TenantResult:
        def mappings(self):
            return self

        def first(self):
            return {"id": TENANT_ID, "slug": "dogfood", "hipaa_mode": False}

    class _JwtDb(_FakeSession):
        async def execute(self, *_args, **_kwargs):
            return _TenantResult()

    @asynccontextmanager
    async def jwt_session_maker():
        yield _JwtDb()

    monkeypatch.setattr(jwt_validation, "async_session_maker", jwt_session_maker)
    monkeypatch.setattr(jwt_validation, "_user_is_active_member", not_member)
    monkeypatch.setattr(
        jwt_validation,
        "verify_brigade_jwt",
        lambda _token: {
            "workspace_id": str(TENANT_ID),
            "sub": "non-member",
            "scopes": ["contacts:read"],
        },
    )

    middleware = jwt_validation.JWTValidationMiddleware(app=lambda *_: None, settings=settings)
    assert await middleware._validate_brigade("token") is None
