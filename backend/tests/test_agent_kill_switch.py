"""Workspace agent kill-switch: BaseAgent.execute honors tenants.agents_paused.

Covers the master switch added in migration 0044 and enforced in
BaseAgent.execute right beside the circuit breaker:

* a paused workspace makes every agent refuse to run for that tenant
  (WorkspaceAgentsPausedError), before any action_event is written
* the default (not paused) lets the agent run normally
* a pause in one tenant does not leak into a different tenant

Mirrors tests/test_agent_base.py (EchoAgent fixture + breaker stub + the
test-container Postgres built by alembic upgrade head).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from contact_ops.agents.base import AgentContext, Visibility
from contact_ops.agents.cost_guard import CostBudget, CostGuard
from contact_ops.agents.errors import WorkspaceAgentsPausedError
from contact_ops.agents.registry import _clear_registry_for_tests
from tests.fixtures.echo_agent import register_echo_agent


@pytest.fixture(autouse=True)
def _clear_registry():
    _clear_registry_for_tests()
    yield
    _clear_registry_for_tests()


def _make_breaker_stub():
    """A circuit breaker stub that never trips (isolates the pause check)."""
    stub = MagicMock()
    stub.check = AsyncMock(return_value=None)
    stub.evaluate = AsyncMock(return_value=None)
    return stub


async def _make_ctx(*, db_session, tenant_id: str, event_payload: dict) -> AgentContext:
    return AgentContext(
        db=db_session,
        audit_db=db_session,
        tenant_id=uuid.UUID(tenant_id),
        visibility=Visibility.PRIVATE,
        triggered_by="manual",
        event_payload=event_payload,
        cost_guard=CostGuard(db=db_session, budget=CostBudget()),
        circuit_breaker=_make_breaker_stub(),
    )


async def _seed_tenant(
    db_session, tid: str, slug: str, *, agents_paused: bool = False
) -> None:
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix, agents_paused)
            VALUES (CAST(:id AS uuid), :slug, 'brand', :slug, CAST(:id AS uuid),
                false, :slug, :slug, :paused)
            ON CONFLICT (id) DO UPDATE SET agents_paused = EXCLUDED.agents_paused
            """
        ),
        {"id": tid, "slug": slug, "paused": agents_paused},
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_paused_workspace_refuses_to_run(db_session):
    tid = "00000000-0000-0000-0000-00000000aa01"
    await _seed_tenant(db_session, tid, "paused-ws", agents_paused=True)
    agent = register_echo_agent()
    ctx = await _make_ctx(
        db_session=db_session,
        tenant_id=tid,
        event_payload={"message": "blocked", "confidence": 0.5},
    )
    with pytest.raises(WorkspaceAgentsPausedError):
        await agent.execute(ctx=ctx)

    # The pause check fires before _run, so no action_event is written.
    count = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM action_event "
                "WHERE tenant_id = CAST(:t AS uuid) AND event_type = 'echo.propose'"
            ),
            {"t": tid},
        )
    ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_unpaused_workspace_runs_normally(db_session):
    tid = "00000000-0000-0000-0000-00000000bb01"
    await _seed_tenant(db_session, tid, "active-ws", agents_paused=False)
    agent = register_echo_agent()
    ctx = await _make_ctx(
        db_session=db_session,
        tenant_id=tid,
        event_payload={"message": "ok", "confidence": 0.5},
    )
    result = await agent.execute(ctx=ctx)
    assert result.proposals_emitted == 1


@pytest.mark.asyncio
async def test_pause_is_tenant_scoped(db_session):
    paused = "00000000-0000-0000-0000-00000000aa02"
    active = "00000000-0000-0000-0000-00000000bb02"
    await _seed_tenant(db_session, paused, "paused-two", agents_paused=True)
    await _seed_tenant(db_session, active, "active-two", agents_paused=False)
    agent = register_echo_agent()

    with pytest.raises(WorkspaceAgentsPausedError):
        await agent.execute(
            ctx=await _make_ctx(
                db_session=db_session,
                tenant_id=paused,
                event_payload={"message": "x", "confidence": 0.5},
            )
        )

    # A pause on one tenant must not block a different tenant.
    result = await agent.execute(
        ctx=await _make_ctx(
            db_session=db_session,
            tenant_id=active,
            event_payload={"message": "y", "confidence": 0.5},
        )
    )
    assert result.proposals_emitted == 1
