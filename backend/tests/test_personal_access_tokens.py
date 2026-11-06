from __future__ import annotations

import uuid

import pytest

from contact_ops.mcp.registry import MCPContext, list_tools
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.personal_access_tokens import (
    IssuePersonalAccessTokenInput,
    ListPersonalAccessTokensInput,
    RevokePersonalAccessTokenInput,
    issue_personal_access_token,
    list_personal_access_tokens,
    revoke_personal_access_token,
)
from contact_ops.pat_scopes import PAT_SCOPE_CATALOGUE
from tests.test_dedup_agent import _tenant

pytestmark = pytest.mark.asyncio


def test_pat_scope_catalogue_covers_registered_tools() -> None:
    register_all_tools()
    registered = {scope for tool in list_tools() for scope in tool.required_scopes}
    assert registered <= set(PAT_SCOPE_CATALOGUE)
    assert "contacts:read_personal" in PAT_SCOPE_CATALOGUE


async def test_pat_issue_list_revoke(db_session):
    tenant_id = uuid.uuid4()
    await _tenant(db_session, tenant_id)
    ctx = MCPContext(
        tenant_id=tenant_id,
        user_id="aaron",
        actor_chain={"sub": "aaron"},
        human_authority=str(uuid.uuid4()),
        db=db_session,
        audit_db=db_session,
        request_id="test",
        claims={
            "sub": "aaron",
            "uc_uid": "aaron",
            "scope": "pat:read pat:write",
            "realm_access": {"roles": ["STAFF"]},
        },
    )

    issued = await issue_personal_access_token(
        ctx,
        IssuePersonalAccessTokenInput(display_name="Claude"),
    )
    listed = await list_personal_access_tokens(ctx, ListPersonalAccessTokensInput())
    revoked = await revoke_personal_access_token(
        ctx,
        RevokePersonalAccessTokenInput(pat_id=issued.pat_id),
    )

    assert issued.token_plaintext.startswith("co_pat_")
    assert len(issued.token_plaintext) == 47
    assert listed.items[0].last_4 == issued.last_4
    assert revoked.status == "revoked"
