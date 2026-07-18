"""GraphSyncWorker drains graph_sync_outbox into tenant FalkorDB graphs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents import AgentClass, AgentContext, AgentDef, AgentResult, BaseAgent, TrustTier
from contact_ops.agents.graph_sync.cypher_writes import build_edge_event_write, build_write
from contact_ops.agents.graph_sync.falkordb_client import (
    FalkorDBGraphClient,
    TenantGraph,
    graph_name_for_slug,
)
from contact_ops.agents.graph_sync.retry_policy import decide_retry
from contact_ops.agents.registry import register_agent

# The dedup merge tools and the voice-match resolver publish edge writes to
# ``event_outbox`` (NOT ``graph_sync_outbox``, which has no producer on this
# branch) under these two channels. ``drain_edge_events`` applies them.
EDGE_SYNC_CHANNELS: tuple[str, ...] = ("falkordb.edge.create", "graph.sync_edge")

logger = structlog.get_logger(__name__)

GRAPH_SYNC_WORKER_DEF = AgentDef(
    slug="graph-sync-worker",
    name="Graph Sync Worker",
    version="1.0.0",
    agent_class=AgentClass.CONTINUOUS,
    description="Drains graph_sync_outbox into per-tenant FalkorDB graphs.",
    cost_budget_monthly_cents=500,
    initial_trust_tier=TrustTier.T2_TRUSTED,
    triggers=("continuous",),
    declared_capabilities=("graph_sync_outbox.claim", "falkordb.write"),
)


@dataclass(frozen=True)
class GraphOutboxRow:
    id: UUID
    entity_kind: str
    entity_id: UUID
    op: str
    payload: dict[str, Any]
    tenant_id: UUID
    graph_name: str
    attempts: int


class GraphSyncWorker(BaseAgent):
    async def _run(self, ctx: AgentContext) -> AgentResult:
        drained = await drain_pending_outbox(db=ctx.db)
        return AgentResult(
            proposals_emitted=0,
            proposals_auto_applied=0,
            decision_summary=f"graph sync drained {drained} outbox rows",
            extra={"rows_drained": drained},
        )


register_agent(GRAPH_SYNC_WORKER_DEF)


async def drain_pending_outbox(
    *,
    db: AsyncSession,
    client: FalkorDBGraphClient | None = None,
    batch_size: int = 100,
) -> int:
    own_client = client is None
    graph_client = client or FalkorDBGraphClient()
    try:
        rows = await claim_pending_rows(db=db, batch_size=batch_size)
        processed = 0
        for row in rows:
            if await process_outbox_row(db=db, client=graph_client, row=row):
                processed += 1
        await db.commit()
        return processed
    finally:
        if own_client:
            await graph_client.close()


async def _resolve_tenant_graph(*, db: AsyncSession, tenant_id: UUID) -> TenantGraph | None:
    """Resolve a tenant's FalkorDB target graph. None if the tenant is gone.

    ``event_outbox`` rows carry only ``tenant_id`` (unlike ``graph_sync_outbox``
    which stores ``graph_name`` inline), so we look up the graph the same way the
    synchronous appliers do: ``tenants.graph_name`` or a slug-derived default.
    """
    from contact_ops.models import Tenant

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        return None
    graph_name = tenant.graph_name or graph_name_for_slug(tenant.slug)
    return TenantGraph(
        tenant_id=tenant_id, graph_name=graph_name, graph_mode=tenant.graph_mode
    )


async def drain_edge_events(
    *,
    db: AsyncSession,
    client: FalkorDBGraphClient | None = None,
    batch_size: int = 100,
) -> int:
    """Drain the FalkorDB edge-sync channels of ``event_outbox`` into tenant graphs.

    The dedup merge tools (channel ``falkordb.edge.create``) and the voice-match
    resolver (channel ``graph.sync_edge``) publish edge operations to
    ``event_outbox`` — but nothing applied them, so those rows accumulated forever
    while the ``graph_sync_outbox`` drain (no producer on this branch) ran empty.
    This claims those rows through the existing :class:`EventOutbox` consumer,
    writes each edge into the row's tenant graph, and marks it processed.

    Idempotent: every write is a MERGE/DELETE, and a handler failure leaves
    ``processed_at`` NULL so the row is re-claimed on the next tick.
    """
    from contact_ops.agents.outbox import EventOutbox

    # claim_batch/mark_processed operate on the passed ``db``; EventOutbox only
    # uses its engine for the LISTEN/sweeper loops we do not run here. Reuse the
    # session's own bind so no extra engine is created.
    outbox = EventOutbox(engine=db.bind)  # type: ignore[arg-type]
    own_client = client is None
    graph_client = client or FalkorDBGraphClient()
    graph_cache: dict[UUID, TenantGraph | None] = {}
    processed = 0
    try:
        for channel in EDGE_SYNC_CHANNELS:
            messages = await outbox.claim_batch(
                db=db,
                channel=channel,
                consumer_id="graph-sync-worker",
                limit=batch_size,
                min_age_seconds=0,
            )
            for msg in messages:
                if msg.tenant_id not in graph_cache:
                    graph_cache[msg.tenant_id] = await _resolve_tenant_graph(
                        db=db, tenant_id=msg.tenant_id
                    )
                tenant_graph = graph_cache[msg.tenant_id]
                if tenant_graph is None:
                    # Tenant deleted (row is CASCADE-orphaned or mid-teardown):
                    # the edge is advisory, so consume it rather than wedge the
                    # channel behind a row that can never resolve.
                    await outbox.mark_processed(db=db, outbox_id=msg.id)
                    processed += 1
                    continue
                try:
                    write = build_edge_event_write(channel, msg.payload)
                    await graph_client.query(tenant_graph, write.cypher, write.params)
                    await outbox.mark_processed(db=db, outbox_id=msg.id)
                    processed += 1
                except Exception as exc:  # noqa: BLE001 — leave NULL for re-claim
                    logger.warning(
                        "graph_edge_sync_failed",
                        channel=channel,
                        outbox_id=msg.id,
                        error=str(exc),
                    )
        await db.commit()
        return processed
    finally:
        if own_client:
            await graph_client.close()


async def claim_pending_rows(*, db: AsyncSession, batch_size: int) -> list[GraphOutboxRow]:
    result = await db.execute(
        text(
            """
            SELECT id, entity_kind, entity_id, op, payload, tenant_id, graph_name, attempts
            FROM graph_sync_outbox
            WHERE status = 'pending'
            ORDER BY created_at
            LIMIT :batch_size
            FOR UPDATE SKIP LOCKED
            """
        ),
        {"batch_size": batch_size},
    )
    rows: list[GraphOutboxRow] = []
    for row in result.mappings():
        rows.append(
            GraphOutboxRow(
                id=row["id"],
                entity_kind=row["entity_kind"],
                entity_id=row["entity_id"],
                op=row["op"],
                payload=dict(row["payload"] or {}),
                tenant_id=row["tenant_id"],
                graph_name=row["graph_name"],
                attempts=int(row["attempts"] or 0),
            )
        )
    return rows


async def process_outbox_row(
    *,
    db: AsyncSession,
    client: FalkorDBGraphClient,
    row: GraphOutboxRow,
) -> bool:
    tenant_graph = TenantGraph(tenant_id=row.tenant_id, graph_name=row.graph_name)
    try:
        write = build_write(row.entity_kind, row.op, row.payload)
        await client.query(tenant_graph, write.cypher, write.params)
    except Exception as exc:
        attempts = row.attempts + 1
        decision = decide_retry(attempts)
        await db.execute(
            text(
                """
                UPDATE graph_sync_outbox
                SET attempts = :attempts,
                    last_attempt_at = :now,
                    last_error = :error,
                    status = :status
                WHERE id = :id
                """
            ),
            {
                "id": row.id,
                "attempts": attempts,
                "now": datetime.now(UTC),
                "error": str(exc)[:2000],
                "status": decision.next_status,
            },
        )
        if decision.promote_to_dlq:
            await promote_to_dlq(db=db, row=row, error=str(exc), attempts=attempts)
        logger.warning(
            "graph_sync_row_failed",
            outbox_id=str(row.id),
            attempts=attempts,
            error=str(exc),
        )
        return False

    await db.execute(
        text(
            """
            UPDATE graph_sync_outbox
            SET status = 'sent',
                attempts = attempts + 1,
                last_attempt_at = :now,
                last_error = NULL
            WHERE id = :id
            """
        ),
        {"id": row.id, "now": datetime.now(UTC)},
    )
    return True


async def promote_to_dlq(
    *,
    db: AsyncSession,
    row: GraphOutboxRow,
    error: str,
    attempts: int,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO graph_sync_dlq (
                outbox_id, tenant_id, graph_name, entity_kind, entity_id, op,
                payload, attempts, last_error
            )
            VALUES (
                :outbox_id, :tenant_id, :graph_name, :entity_kind, :entity_id, :op,
                :payload, :attempts, :last_error
            )
            ON CONFLICT (outbox_id) DO UPDATE
            SET attempts = EXCLUDED.attempts,
                last_error = EXCLUDED.last_error,
                promoted_at = now()
            """
        ),
        {
            "outbox_id": row.id,
            "tenant_id": row.tenant_id,
            "graph_name": row.graph_name,
            "entity_kind": row.entity_kind,
            "entity_id": row.entity_id,
            "op": row.op,
            "payload": row.payload,
            "attempts": attempts,
            "last_error": error[:2000],
        },
    )
