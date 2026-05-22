"""MCP admin tool registration + handler tests.

Covers RBAC (ADMIN-required), the registry shape (annotations + scope),
and the structural happy path for ``list_agents`` and ``get_agent_trust``.
The breaker tools defer to Redis so we focus on contract here; runtime
behavior is exercised by ``test_agent_base.py``.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.agents.registry import _clear_registry_for_tests
from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.agent_admin import (
    GetAgentTrustInput,
    ListAgentsInput,
    _handle_get_agent_trust,
    _handle_list_agents,
)
from tests.fixtures.echo_agent import register_echo_agent


def _ctx(role: str = "ADMIN", scopes: str = "contactops:agents.admin") -> MCPContext:
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    return MCPContext(
        tenant_id=tenant_id,
        user_id=str(uuid.uuid4()),
        actor_chain={"sub": "test-admin"},
        human_authority=str(tenant_id),
        db=cast(Any, None),
        audit_db=cast(Any, None),
        request_id="test-request",
        claims={"realm_access": {"roles": [role]}, "scope": scopes},
    )


def test_seven_admin_tools_registered():
    register_all_tools()
    expected = {
        "list_agents",
        "get_agent_trust",
        "promote_agent_tier",
        "demote_agent_tier",
        "drain_dlq",
        "pause_agent",
        "resume_agent",
    }
    for name in expected:
        assert get_tool(name) is not None, f"{name} not registered"


def test_admin_tools_require_admin_role():
    register_all_tools()
    for name in (
        "list_agents",
        "get_agent_trust",
        "promote_agent_tier",
        "demote_agent_tier",
        "drain_dlq",
        "pause_agent",
        "resume_agent",
    ):
        tool = get_tool(name)
        assert tool is not None
        assert tool.required_role == "ADMIN"
        assert "contactops:agents.admin" in tool.required_scopes


def test_list_agents_readonly_annotations():
    register_all_tools()
    tool = get_tool("list_agents")
    assert tool is not None
    assert tool.annotations["readOnlyHint"] is True
    assert tool.annotations["destructiveHint"] is False


def test_pause_agent_destructive_annotations():
    register_all_tools()
    tool = get_tool("pause_agent")
    assert tool is not None
    assert tool.annotations["destructiveHint"] is True
    assert tool.annotations["idempotentHint"] is True


@pytest.mark.asyncio
async def test_list_agents_handler_returns_registry():
    _clear_registry_for_tests()
    register_echo_agent(slug="echo-admin-test")
    try:
        ctx = _ctx()
        out = await _handle_list_agents(ctx, ListAgentsInput())
        slugs = {a.slug for a in out.agents}
        assert "echo-admin-test" in slugs
        assert out.count >= 1
        echo = next(a for a in out.agents if a.slug == "echo-admin-test")
        assert echo.agent_class == "batch"
        assert echo.initial_trust_tier_label.startswith("T0")
    finally:
        _clear_registry_for_tests()


@pytest.mark.asyncio
async def test_get_agent_trust_returns_none_for_unseeded(db_session):
    """When no agent_trust row exists, found=False."""
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    ctx = MCPContext(
        tenant_id=tenant_id,
        user_id=str(uuid.uuid4()),
        actor_chain={"sub": "test-admin"},
        human_authority=str(tenant_id),
        db=db_session,
        audit_db=db_session,
        request_id="r",
        claims={"realm_access": {"roles": ["ADMIN"]}, "scope": "contactops:agents.admin"},
    )
    out = await _handle_get_agent_trust(
        ctx, GetAgentTrustInput(agent_slug="nonexistent", visibility="private")
    )
    assert out.found is False
    assert out.trust is None
