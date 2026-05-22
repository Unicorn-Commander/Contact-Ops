"""Event outbox: durable LISTEN/NOTIFY backstop (Phase 3 Design §6).

Postgres ``NOTIFY`` is transactionally consistent but lossy: only currently
connected listeners receive the payload. The outbox makes events durable:

  1. ``publish(...)`` inserts an ``event_outbox`` row in the same transaction
     as the originating domain change.
  2. A trigger (or the publisher itself) fires ``pg_notify`` after commit.
  3. Subscribers run two loops in parallel:
        a. LISTEN — picks up events as they fire, fast path.
        b. Polling sweeper — every ``poll_interval`` seconds, claims any
           ``WHERE processed_at IS NULL AND created_at < now() - 30s`` rows
           via ``FOR UPDATE SKIP LOCKED``, handles them, marks consumed.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class OutboxMessage:
    """Decoded outbox payload returned to subscriber handlers."""

    id: int
    channel: str
    tenant_id: UUID
    payload: dict[str, Any]
    created_at: datetime


class EventOutbox:
    """Postgres-backed durable outbox.

    Construct once per process; can be shared across subscribers. Each call
    to ``publish`` runs in the caller's transaction (we receive the
    ``AsyncSession``). Sweeper loops own their own short transactions.
    """

    def __init__(self, *, engine: AsyncEngine, sweeper_interval_s: float = 10.0) -> None:
        self.engine = engine
        self.sweeper_interval_s = sweeper_interval_s

    async def publish(
        self,
        *,
        db: AsyncSession,
        channel: str,
        payload: dict[str, Any],
        tenant_id: UUID,
    ) -> int:
        """Insert into ``event_outbox`` inside the caller's transaction.

        The NOTIFY happens via the table trigger ``event_outbox_notify``
        after commit, so subscribers that LISTEN see the row at the same
        moment any joined domain row becomes visible. Returns the
        outbox row id.
        """
        result = await db.execute(
            text(
                """
                INSERT INTO event_outbox (channel, tenant_id, payload)
                VALUES (:channel, CAST(:tenant_id AS uuid), CAST(:payload AS jsonb))
                RETURNING id
                """
            ),
            {
                "channel": channel,
                "tenant_id": str(tenant_id),
                "payload": json.dumps(payload, sort_keys=True, default=_json_default),
            },
        )
        return int(result.scalar_one())

    async def claim_batch(
        self,
        *,
        db: AsyncSession,
        channel: str,
        consumer_id: str,
        limit: int = 100,
        min_age_seconds: int = 30,
    ) -> list[OutboxMessage]:
        """Atomically claim a batch of unprocessed events.

        Uses ``FOR UPDATE SKIP LOCKED`` so concurrent sweepers do not
        double-process. ``min_age_seconds`` gates the sweeper to events
        older than the LISTEN/NOTIFY fast path's window — there is no
        point claiming an event that the fast path is about to handle.
        """
        result = await db.execute(
            text(
                """
                WITH claim AS (
                    SELECT id
                    FROM event_outbox
                    WHERE channel = :channel
                      AND processed_at IS NULL
                      AND created_at <= now() - make_interval(secs => :min_age)
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT :limit
                )
                UPDATE event_outbox
                SET notify_attempted = TRUE
                FROM claim
                WHERE event_outbox.id = claim.id
                RETURNING event_outbox.id, event_outbox.channel,
                          event_outbox.tenant_id, event_outbox.payload,
                          event_outbox.created_at
                """
            ),
            {
                "channel": channel,
                "min_age": min_age_seconds,
                "limit": limit,
            },
        )
        rows = result.mappings().all()
        return [
            OutboxMessage(
                id=int(row["id"]),
                channel=str(row["channel"]),
                tenant_id=UUID(str(row["tenant_id"])),
                payload=(
                    row["payload"]
                    if isinstance(row["payload"], dict)
                    else json.loads(row["payload"])
                ),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def mark_processed(
        self,
        *,
        db: AsyncSession,
        outbox_id: int,
    ) -> None:
        """Mark a claimed event as consumed."""
        await db.execute(
            text(
                """
                UPDATE event_outbox
                SET processed_at = now()
                WHERE id = :id
                """
            ),
            {"id": outbox_id},
        )

    async def subscribe(
        self,
        *,
        channel: str,
        consumer_id: str,
        handler: Callable[[OutboxMessage], Awaitable[None]],
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Long-running subscriber loop combining LISTEN and polling.

        Two tasks run concurrently:

        * ``_listen`` keeps a dedicated asyncpg connection LISTENing on
          ``channel`` and dispatches payloads as they arrive (fast path).
        * ``_sweep`` polls ``event_outbox`` for any unprocessed events
          older than ``min_age_seconds`` and dispatches them (durability).

        Either path acquires the row exclusively via ``claim_batch`` before
        invoking ``handler``, so the handler only ever sees an event once.
        """
        stop_event = stop_event or asyncio.Event()

        async def _sweep() -> None:
            while not stop_event.is_set():
                try:
                    async with AsyncSession(self.engine) as db:
                        msgs = await self.claim_batch(
                            db=db,
                            channel=channel,
                            consumer_id=consumer_id,
                            min_age_seconds=30,
                        )
                        for msg in msgs:
                            try:
                                await handler(msg)
                                await self.mark_processed(
                                    db=db, outbox_id=msg.id
                                )
                            except Exception as exc:
                                logger.error(
                                    "outbox_handler_failed",
                                    channel=channel,
                                    outbox_id=msg.id,
                                    error=str(exc),
                                )
                        await db.commit()
                except Exception as exc:
                    logger.error(
                        "outbox_sweep_failed",
                        channel=channel,
                        error=str(exc),
                    )
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.sweeper_interval_s,
                    )
                except TimeoutError:
                    continue

        async def _listen() -> None:
            """LISTEN loop. Re-fires the sweeper when a NOTIFY arrives.

            We do not consume the notify payload directly; the sweeper
            owns the only path that calls ``claim_batch``. The LISTEN
            channel exists to bypass the ``min_age_seconds`` wait.
            """
            raw_conn = await self.engine.raw_connection()
            try:
                asyncpg_conn: Any = await _unwrap_asyncpg(raw_conn)
                if asyncpg_conn is None:
                    logger.warning(
                        "outbox_listen_unavailable",
                        channel=channel,
                        reason="non-asyncpg connection",
                    )
                    return
                queue: asyncio.Queue[str] = asyncio.Queue()

                def _on_notify(
                    connection: Any,
                    pid: int,
                    listen_channel: str,
                    payload: str,
                ) -> None:
                    with suppress(Exception):
                        queue.put_nowait(payload)

                await asyncpg_conn.add_listener(channel, _on_notify)
                try:
                    while not stop_event.is_set():
                        try:
                            await asyncio.wait_for(queue.get(), timeout=1.0)
                        except TimeoutError:
                            continue
                        # Fast-path: as soon as a NOTIFY arrives, kick the
                        # sweeper to claim the row immediately (no 30s wait).
                        async with AsyncSession(self.engine) as db:
                            msgs = await self.claim_batch(
                                db=db,
                                channel=channel,
                                consumer_id=consumer_id,
                                min_age_seconds=0,
                            )
                            for msg in msgs:
                                try:
                                    await handler(msg)
                                    await self.mark_processed(
                                        db=db, outbox_id=msg.id
                                    )
                                except Exception as exc:
                                    logger.error(
                                        "outbox_handler_failed",
                                        channel=channel,
                                        outbox_id=msg.id,
                                        error=str(exc),
                                    )
                            await db.commit()
                finally:
                    with suppress(Exception):
                        await asyncpg_conn.remove_listener(channel, _on_notify)
            finally:
                raw_conn.close()

        await asyncio.gather(_sweep(), _listen())


async def _unwrap_asyncpg(raw_conn: Any) -> Any:
    """Return the underlying asyncpg.Connection for LISTEN, or None.

    SQLAlchemy wraps the asyncpg connection in an ``AsyncAdaptedQueuePool``
    proxy. The asyncpg connection itself is reachable via several private
    paths depending on driver version; we probe them in order and return
    None if none match (the LISTEN fast path is then disabled and we fall
    back to pure polling).
    """
    candidate = getattr(raw_conn, "driver_connection", None)
    if candidate is not None:
        return candidate
    candidate = getattr(raw_conn, "_connection", None)
    if candidate is not None:
        return candidate
    return None


def _json_default(obj: Any) -> str:
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.astimezone(UTC).isoformat()
    raise TypeError(f"not JSON-serializable: {type(obj)!r}")
