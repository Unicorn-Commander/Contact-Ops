from __future__ import annotations

import uuid

import pytest

from contact_ops.agents.graph_sync.falkordb_client import GraphQueryResult
from contact_ops.agents.graph_sync.worker import GraphOutboxRow, process_outbox_row


class FakeDB:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
        self.statements.append((str(statement), params or {}))


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.queries: list[tuple[str, dict]] = []

    async def query(self, tenant_graph, cypher, params):  # type: ignore[no-untyped-def]
        if self.fail:
            raise RuntimeError("falkor down")
        self.queries.append((cypher, params))
        return GraphQueryResult(header=[], rows=[], statistics=[])


def _row(attempts: int = 0) -> GraphOutboxRow:
    return GraphOutboxRow(
        id=uuid.uuid4(),
        entity_kind="person",
        entity_id=uuid.uuid4(),
        op="upsert",
        payload={
            "id": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "display_name": "Aaron",
            "given_name": "Aaron",
            "family_name": None,
            "primary_email": None,
            "primary_phone": None,
            "linkedin_url": None,
            "updated_at": "2026-05-23T00:00:00Z",
            "confidence": 1.0,
            "provenance_event_id": None,
        },
        tenant_id=uuid.uuid4(),
        graph_name="contact_ops__aaron_personal",
        attempts=attempts,
    )


@pytest.mark.asyncio
async def test_process_outbox_row_marks_sent() -> None:
    db = FakeDB()
    client = FakeClient()
    assert await process_outbox_row(db=db, client=client, row=_row())
    assert client.queries
    assert any("status = 'sent'" in statement for statement, _ in db.statements)


@pytest.mark.asyncio
async def test_process_outbox_row_promotes_after_five_failures() -> None:
    db = FakeDB()
    client = FakeClient(fail=True)
    assert not await process_outbox_row(db=db, client=client, row=_row(attempts=4))
    assert any("INSERT INTO graph_sync_dlq" in statement for statement, _ in db.statements)
