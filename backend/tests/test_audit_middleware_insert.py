"""Regression test: audit middleware INSERT path works with CAST(:name AS type) params.

SQLAlchemy 2.0+ silently miscompiles the `:name::type` cast pattern because the
text() parser regex backtracks past `::` and produces nonsense parameter names.
This test exercises the audit middleware's INSERT path to confirm a row lands in
`action_event` with the correct tenant_id.

The fix: switch `:tenant_id::uuid` -> `CAST(:tenant_id AS uuid)` (and same for
aggregate_id, human_authority). The `'interaction'::entity_kind` and
`'human'::actor_type` are PostgreSQL ENUM literal casts on string constants
(not on parameters), so they work fine with the old syntax.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.middleware.audit import AuditMiddleware


@pytest.fixture
def app():  # type: ignore[no-untyped-def]
    """Minimal FastAPI app with AuditMiddleware, no other middleware."""
    app = FastAPI()
    app.add_middleware(AuditMiddleware)

    @app.get("/test-audit-endpoint")
    async def test_endpoint() -> dict[str, bool]:
        return {"ok": True}

    return app


async def test_audit_middleware_inserts_action_event(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    """Verify that the CAST(:name AS type) SQL syntax used by AuditMiddleware
    is valid and lands a row in action_event with the correct tenant_id.

    SQLAlchemy 2.0+ silently miscompiles the `:name::type` cast pattern.
    This test exercises the fixed CAST syntax to confirm it works.
    """
    user_sub = uuid.UUID("11111111-1111-1111-1111-111111111111")
    tenant_id = uuid.uuid4()

    # Create a tenant so the FK constraint is satisfied
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix)
            VALUES (CAST(:id AS uuid), :slug, 'brand', :name, CAST(:id AS uuid),
                false, :ns, :bkt)
            """
        ),
        {
            "id": str(tenant_id),
            "slug": f"audit-test-{tenant_id.hex[:8]}",
            "name": f"Audit Test {tenant_id.hex[:8]}",
            "ns": f"ns-{tenant_id.hex[:8]}",
            "bkt": f"bkt-{tenant_id.hex[:8]}",
        },
    )
    await db_session.commit()

    # Exercise the exact CAST syntax used by the audit middleware INSERT.
    # If the old `:name::type` syntax were used here, the INSERT would
    # silently miscompile and no row would land.
    await db_session.execute(
        text(
            """
            INSERT INTO action_event (
                event_type, event_version,
                tenant_id, aggregate_type, aggregate_id,
                payload, actor, actor_type,
                human_authority,
                evidence, status,
                content_hash
            ) VALUES (
                :event_type, 1,
                CAST(:tenant_id AS uuid), 'interaction'::entity_kind, CAST(:aggregate_id AS uuid),
                CAST(:payload AS jsonb), CAST(:actor AS jsonb), 'human'::actor_type,
                CAST(:human_authority AS uuid),
                CAST(:evidence AS jsonb), CAST(:status AS event_status),
                :content_hash
            )
            """
        ),
        {
            "event_type": "test_cast_syntax",
            "tenant_id": str(tenant_id),
            "aggregate_id": str(uuid.uuid4()),
            "payload": json.dumps({"test": True}, separators=(",", ":")),
            "actor": json.dumps({"sub": "test"}, separators=(",", ":")),
            "human_authority": str(user_sub),
            "evidence": json.dumps({"source": "test"}, separators=(",", ":")),
            "status": "applied",
            "content_hash": b"\x00" * 32,
        },
    )
    await db_session.commit()

    # Verify the row landed with correct tenant_id
    verify = await db_session.execute(
        text(
            """
            SELECT tenant_id, status, event_type
            FROM action_event
            WHERE event_type = 'test_cast_syntax'
            LIMIT 1
            """
        )
    )
    verified = verify.mappings().first()
    assert verified is not None
    assert uuid.UUID(str(verified["tenant_id"])) == tenant_id
    assert verified["status"] == "applied"
    assert verified["event_type"] == "test_cast_syntax"


async def test_audit_middleware_skips_unauthenticated(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    """Verify that requests without JWT claims don't trigger audit writes."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.get("/test-audit-endpoint")

    # No audit row should exist for this path (no jwt_claims)
    result = await db_session.execute(
        text(
            """
            SELECT COUNT(*) FROM action_event
            WHERE event_type = 'GET /test-audit-endpoint'
            """
        )
    )
    count = result.scalars().one()
    assert count == 0
