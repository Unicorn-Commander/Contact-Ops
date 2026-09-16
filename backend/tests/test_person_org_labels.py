import uuid

import pytest
from sqlalchemy import event, text

from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.people import (
    ListPeopleInput,
    SearchPeopleInput,
    list_people,
    search_people,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("search", [False, True])
async def test_organization_labels_are_batched_and_tenant_fenced(
    db_session, seeded_tenants, search
):
    own, foreign = seeded_tenants["non_hipaa"], seeded_tenants["hipaa"]
    visible, hidden = uuid.uuid4(), uuid.uuid4()
    for oid, tid, name in [
        (visible, own, "Visible synthetic employer"),
        (hidden, foreign, "Foreign synthetic employer"),
    ]:
        await db_session.execute(
            text(
                "INSERT INTO organizations (id, legal_name, "
                "display_name, canonical_owner_tenant_id) "
                "VALUES (:id, :name, :name, :tid)"
            ),
            {"id": oid, "name": name, "tid": tid},
        )
    pids = []
    for index in range(26):
        pid = uuid.uuid4()
        pids.append(pid)
        await db_session.execute(
            text(
                "INSERT INTO persons (id, display_name, canonical_owner_tenant_id, current_org_id) "
                "VALUES (:id, :name, :tid, :oid)"
            ),
            {
                "id": pid,
                "name": f"Synthetic {index:02d}",
                "tid": own,
                "oid": hidden if index == 25 else visible,
            },
        )
    no_org = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
                "VALUES (:id, 'Synthetic Unaffiliated', :tid)"
        ),
        {"id": no_org, "tid": own},
    )
    ctx = MCPContext(
        tenant_id=own,
        user_id=str(own),
        actor_chain={},
        human_authority=str(own),
        db=db_session,
        audit_db=db_session,
        request_id="synthetic-label-test",
        claims={"realm_access": {"roles": ["CLIENT"]}, "scope": "person:read"},
    )
    statements = []

    def count_query(conn, cursor, statement, parameters, context, executemany):
        if "FROM organizations" in statement:
            statements.append(statement)

    engine = db_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", count_query)
    try:
        result = await (
            search_people(ctx, SearchPeopleInput(query="Synthetic", limit=100))
            if search
            else list_people(ctx, ListPeopleInput(has_email=False, limit=100))
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_query)
    rows = {x.person_id: x for x in result.items}
    assert len(rows) == 27
    assert all(rows[p].current_org_name == "Visible synthetic employer" for p in pids[:25])
    assert rows[pids[25]].current_org_name is None
    assert rows[no_org].current_org_name is None
    assert len(statements) == 1  # 27 people still incur only one organization query.
