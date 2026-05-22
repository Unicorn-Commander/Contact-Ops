from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel
from sqlalchemy import text

import testcontainers.core.utils

testcontainers.core.utils.raise_for_deprecated_parameter = lambda *_args, **_kwargs: None

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError, error_result
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext, ToolDef, get_tool, list_tools, register_tool


class _In(BaseModel):
    value: str


class _Out(BaseModel):
    ok: bool


async def _handler(ctx: MCPContext, args: BaseModel) -> BaseModel:
    return _Out(ok=True)


@pytest.fixture(autouse=True)
async def _tenant(db_session):
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                qdrant_namespace, garage_bucket_prefix)
            VALUES (:id, 'mcp-test', 'personal', 'MCP Test', :id,
                'mcp-test', 'mcp-test')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": uuid.UUID("00000000-0000-0000-0000-000000000001")},
    )
    await db_session.flush()


def _ctx(db_session) -> MCPContext:
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    return MCPContext(
        tenant_id=tenant_id,
        user_id=str(tenant_id),
        actor_chain={"sub": str(tenant_id)},
        human_authority=str(tenant_id),
        db=db_session,
        audit_db=db_session,
        request_id="test-request",
        claims={
            "sub": str(tenant_id),
            "realm_access": {"roles": ["STAFF"]},
            "scope": "person:read person:write",
        },
    )


def test_registry_register_get_list():
    name = f"unit_test_tool_{uuid.uuid4().hex}"
    register_tool(
        ToolDef(
            name=name,
            description="unit test tool",
            input_model=_In,
            output_model=_Out,
            handler=_handler,
            required_role="CLIENT",
            required_scopes=(),
            annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            idempotency="none",
        )
    )
    assert get_tool(name) is not None
    assert name in {tool.name for tool in list_tools()}


def test_error_result_shape():
    result = error_result(ToolError("PERSON_NOT_FOUND", "missing", hint="check id"))
    assert result["isError"] is True
    assert result["structuredContent"] == {"code": "PERSON_NOT_FOUND", "retryable": False, "hint": "check id"}


def test_rbac_role_and_scope_reject(db_session):
    ctx = _ctx(db_session)
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("person:read",))
    ctx.claims["realm_access"] = {"roles": ["CLIENT"]}
    with pytest.raises(ToolError) as exc:
        require_role(ctx, "STAFF")
    assert exc.value.code == INSUFFICIENT_ROLE
    with pytest.raises(ToolError):
        require_scopes(ctx, ("person:delete",))


@pytest.mark.asyncio
async def test_emit_action_event_writes_row(db_session):
    ctx = _ctx(db_session)
    aggregate_id = uuid.uuid4()
    event_id = await emit_action_event(
        ctx,
        event_type="person.create",
        aggregate_type="person",
        aggregate_id=aggregate_id,
        payload_before=None,
        payload_after={"person_id": str(aggregate_id)},
    )
    await db_session.flush()
    row = await db_session.get(__import__("contact_ops.models").models.ActionEvent, event_id)
    assert row is not None
    assert row.aggregate_id == aggregate_id
