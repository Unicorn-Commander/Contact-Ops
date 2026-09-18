from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, INSUFFICIENT_SCOPE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.identifiers import (
    AddIdentifierInput,
    UpdateIdentifierInput,
    add_identifier,
    update_identifier,
)


def _ctx(role: str = "CLIENT", scopes: str = "") -> MCPContext:
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    return MCPContext(
        tenant_id=tenant_id,
        user_id="test-user",
        actor_chain={"sub": "test-user"},
        human_authority=str(tenant_id),
        db=cast(Any, None),
        audit_db=cast(Any, None),
        request_id="test-request",
        claims={"realm_access": {"roles": [role]}, "scope": scopes},
    )


def test_identifier_tools_registered() -> None:
    register_all_tools()
    for name in {
        "add_identifier",
        "remove_identifier",
        "list_identifiers",
        "verify_identifier",
        "update_identifier",
    }:
        assert get_tool(name) is not None


def test_add_identifier_input_validation() -> None:
    with pytest.raises(ValueError):
        AddIdentifierInput(subject_kind="person", subject_id=uuid.uuid4(), namespace="", value="x")


@pytest.mark.asyncio
async def test_add_identifier_rbac_rejects_client() -> None:
    req = AddIdentifierInput(
        subject_kind="person",
        subject_id=uuid.uuid4(),
        namespace="github.com",
        value="aaron",
    )
    with pytest.raises(ToolError) as exc:
        await add_identifier(_ctx(), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_update_identifier_rbac_rejects_client() -> None:
    req = UpdateIdentifierInput(identifier_id=uuid.uuid4(), verified=True)
    with pytest.raises(ToolError) as exc:
        await update_identifier(_ctx(), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_update_identifier_verified_true_requires_verify_scope() -> None:
    # STAFF with only identifier:write must NOT be able to flip verified on.
    req = UpdateIdentifierInput(identifier_id=uuid.uuid4(), verified=True)
    with pytest.raises(ToolError) as exc:
        await update_identifier(_ctx("STAFF", "identifier:write"), req)
    assert exc.value.code == INSUFFICIENT_SCOPE


@pytest.mark.asyncio
async def test_update_identifier_verified_false_requires_verify_scope() -> None:
    # ...nor flip it back off (the EVIDENCE's exact bypass).
    req = UpdateIdentifierInput(identifier_id=uuid.uuid4(), verified=False)
    with pytest.raises(ToolError) as exc:
        await update_identifier(_ctx("STAFF", "identifier:write"), req)
    assert exc.value.code == INSUFFICIENT_SCOPE


@pytest.mark.asyncio
async def test_update_identifier_verify_scope_holder_passes_the_gate() -> None:
    # With both scopes the verify gate is satisfied; execution proceeds past the
    # scope checks (and fails later on the absent db, NOT with INSUFFICIENT_SCOPE).
    req = UpdateIdentifierInput(identifier_id=uuid.uuid4(), verified=True)
    with pytest.raises(Exception) as exc:
        await update_identifier(_ctx("STAFF", "identifier:write identifier:verify"), req)
    assert not isinstance(exc.value, ToolError) or exc.value.code != INSUFFICIENT_SCOPE


@pytest.mark.asyncio
async def test_update_identifier_non_verified_patch_needs_write_only() -> None:
    # A namespace/label edit that never touches `verified` is still allowed with
    # write scope alone (fails later on the absent db, not on a scope error).
    req = UpdateIdentifierInput(identifier_id=uuid.uuid4(), namespace="github.com")
    with pytest.raises(Exception) as exc:
        await update_identifier(_ctx("STAFF", "identifier:write"), req)
    assert not isinstance(exc.value, ToolError) or exc.value.code != INSUFFICIENT_SCOPE


@pytest.mark.asyncio
async def test_update_identifier_explicit_null_verified_is_no_change() -> None:
    # An explicit `verified: null` means "no change" (NOT NULL column), so it must
    # not trip the verify gate either — write scope alone gets past the check.
    req = UpdateIdentifierInput(identifier_id=uuid.uuid4(), verified=None)
    with pytest.raises(Exception) as exc:
        await update_identifier(_ctx("STAFF", "identifier:write"), req)
    assert not isinstance(exc.value, ToolError) or exc.value.code != INSUFFICIENT_SCOPE


@pytest.mark.asyncio
async def test_add_verified_identifier_requires_verify_scope_before_io():
    req = AddIdentifierInput(
        subject_kind="person",
        subject_id=uuid.uuid4(),
        namespace="synthetic",
        value="example",
        verified=True,
    )
    with pytest.raises(ToolError) as exc:
        await add_identifier(_ctx("STAFF", "identifier:write"), req)
    assert exc.value.code == INSUFFICIENT_SCOPE
