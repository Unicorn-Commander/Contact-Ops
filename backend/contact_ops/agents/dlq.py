"""Dead-letter queue for agent actions (Phase 3 Design §3.4).

Actions that fail after Celery's retry budget land in ``agent_action_dlq``
with an ``error_class`` tag. An admin replays them in bulk via the MCP
``drain_dlq`` tool (see ``mcp/tools/agent_admin``) once the root cause is
fixed. Alerts fire when depth > 100 unresolved or oldest > 24h.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ReplayFn = Callable[["DLQEntry"], Awaitable[None]]


class ErrorClass(str, Enum):
    """Coarse classification of DLQ entries; powers Grafana panels."""

    LLM_5XX = "llm_5xx"
    SCHEMA_VALIDATION = "schema_validation"
    DOWNSTREAM_TIMEOUT = "downstream_timeout"
    PERMISSION_DENIED = "permission_denied"
    CASCADE_BLOCK = "cascade_block"
    BUDGET_EXCEEDED = "budget_exceeded"
    OTHER = "other"

    @classmethod
    def from_exception(cls, exc: BaseException) -> ErrorClass:
        """Best-effort error classification."""
        name = exc.__class__.__name__
        if "Timeout" in name:
            return cls.DOWNSTREAM_TIMEOUT
        if "Validation" in name or "Pydantic" in name:
            return cls.SCHEMA_VALIDATION
        if "Permission" in name or "Forbidden" in name:
            return cls.PERMISSION_DENIED
        if "CostBudget" in name:
            return cls.BUDGET_EXCEEDED
        if "Cascade" in name or "Conflict" in name:
            return cls.CASCADE_BLOCK
        if "Http" in name and "5" in str(exc):
            return cls.LLM_5XX
        return cls.OTHER


@dataclass(frozen=True)
class DLQEntry:
    id: UUID
    original_action_event_id: UUID
    agent_slug: str
    tenant_id: UUID
    error: str
    error_class: ErrorClass
    retry_count: int
    last_attempted_at: datetime
    payload: dict[str, Any]
    replayable: bool


@dataclass(frozen=True)
class ReplayResult:
    replayed: int
    still_failing: int
    skipped_unreplayable: int
    errors: list[str]


class DeadLetterQueue:
    """Persistence + bulk-replay over ``agent_action_dlq``."""

    def __init__(self, *, db: AsyncSession) -> None:
        self.db = db

    async def park(
        self,
        *,
        original_action_event_id: UUID,
        agent_slug: str,
        tenant_id: UUID,
        error: BaseException,
        retry_count: int,
        payload: dict[str, Any],
        replayable: bool = True,
    ) -> UUID:
        """Insert a DLQ entry and return its id."""
        error_class = ErrorClass.from_exception(error)
        result = await self.db.execute(
            text(
                """
                INSERT INTO agent_action_dlq (
                    original_action_event_id, agent_slug, tenant_id,
                    error, error_class, retry_count, last_attempted_at,
                    payload, replayable
                ) VALUES (
                    CAST(:original_event_id AS uuid), :agent_slug, CAST(:tenant_id AS uuid),
                    :error, :error_class, :retry_count, now(),
                    CAST(:payload AS jsonb), :replayable
                )
                RETURNING id
                """
            ),
            {
                "original_event_id": str(original_action_event_id),
                "agent_slug": agent_slug,
                "tenant_id": str(tenant_id),
                "error": str(error)[:8192],
                "error_class": error_class.value,
                "retry_count": retry_count,
                "payload": json.dumps(payload, sort_keys=True, default=str),
                "replayable": replayable,
            },
        )
        return UUID(str(result.scalar_one()))

    async def list_by_error_class(
        self,
        *,
        tenant_id: UUID | None = None,
        error_class: ErrorClass | None = None,
        limit: int = 100,
    ) -> list[DLQEntry]:
        clauses = ["resolved_at IS NULL"]
        params: dict[str, Any] = {"limit": limit}
        if tenant_id is not None:
            clauses.append("tenant_id = CAST(:tenant_id AS uuid)")
            params["tenant_id"] = str(tenant_id)
        if error_class is not None:
            clauses.append("error_class = :error_class")
            params["error_class"] = error_class.value
        where = " AND ".join(clauses)
        # ``where`` is composed only of literal clauses chosen from a fixed
        # allowlist; no caller-supplied SQL fragments are interpolated.
        # Parameter values are still bound through `params`.
        query = (
            "SELECT id, original_action_event_id, agent_slug, tenant_id,\n"
            "       error, error_class, retry_count, last_attempted_at,\n"
            "       payload, replayable\n"
            "FROM agent_action_dlq\n"
            f"WHERE {where}\n"
            "ORDER BY created_at DESC\n"
            "LIMIT :limit"
        )
        result = await self.db.execute(text(query), params)  # noqa: S608
        rows = result.mappings().all()
        return [
            DLQEntry(
                id=UUID(str(row["id"])),
                original_action_event_id=UUID(str(row["original_action_event_id"])),
                agent_slug=str(row["agent_slug"]),
                tenant_id=UUID(str(row["tenant_id"])),
                error=str(row["error"]),
                error_class=ErrorClass(row["error_class"]),
                retry_count=int(row["retry_count"]),
                last_attempted_at=row["last_attempted_at"],
                payload=(
                    row["payload"]
                    if isinstance(row["payload"], dict)
                    else json.loads(row["payload"])
                ),
                replayable=bool(row["replayable"]),
            )
            for row in rows
        ]

    async def replay(
        self,
        *,
        dlq_ids: list[UUID],
        replay_fn: ReplayFn,
    ) -> ReplayResult:
        """Bulk-replay entries by invoking ``replay_fn(entry)``.

        ``replay_fn`` is an async callable that takes a ``DLQEntry`` and
        re-runs the original action. On success it returns; on failure it
        raises. The caller is responsible for ensuring the underlying root
        cause has been fixed; otherwise replays will just re-fail.
        """
        if not dlq_ids:
            return ReplayResult(0, 0, 0, [])

        entries = await self._fetch_by_ids(dlq_ids)
        replayed = 0
        still_failing = 0
        skipped = 0
        errors: list[str] = []

        for entry in entries:
            if not entry.replayable:
                skipped += 1
                continue
            try:
                await replay_fn(entry)
                await self._mark_resolved(
                    entry_id=entry.id,
                    resolution_note="replayed via DLQ.replay",
                )
                replayed += 1
            except Exception as exc:
                still_failing += 1
                errors.append(f"{entry.id}: {exc}")
                await self._bump_retry_count(entry_id=entry.id, error=exc)

        return ReplayResult(
            replayed=replayed,
            still_failing=still_failing,
            skipped_unreplayable=skipped,
            errors=errors,
        )

    async def mark_unreplayable(
        self,
        *,
        entry_id: UUID,
        reason: str,
    ) -> None:
        await self.db.execute(
            text(
                """
                UPDATE agent_action_dlq
                SET replayable = FALSE,
                    resolution_note = :note
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": str(entry_id), "note": reason[:1024]},
        )

    async def _fetch_by_ids(self, ids: list[UUID]) -> list[DLQEntry]:
        result = await self.db.execute(
            text(
                """
                SELECT id, original_action_event_id, agent_slug, tenant_id,
                       error, error_class, retry_count, last_attempted_at,
                       payload, replayable
                FROM agent_action_dlq
                WHERE id = ANY(CAST(:ids AS uuid[]))
                  AND resolved_at IS NULL
                """
            ),
            {"ids": [str(i) for i in ids]},
        )
        rows = result.mappings().all()
        return [
            DLQEntry(
                id=UUID(str(row["id"])),
                original_action_event_id=UUID(str(row["original_action_event_id"])),
                agent_slug=str(row["agent_slug"]),
                tenant_id=UUID(str(row["tenant_id"])),
                error=str(row["error"]),
                error_class=ErrorClass(row["error_class"]),
                retry_count=int(row["retry_count"]),
                last_attempted_at=row["last_attempted_at"],
                payload=(
                    row["payload"]
                    if isinstance(row["payload"], dict)
                    else json.loads(row["payload"])
                ),
                replayable=bool(row["replayable"]),
            )
            for row in rows
        ]

    async def _mark_resolved(
        self,
        *,
        entry_id: UUID,
        resolution_note: str,
    ) -> None:
        await self.db.execute(
            text(
                """
                UPDATE agent_action_dlq
                SET resolved_at = now(),
                    resolution_note = :note
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": str(entry_id), "note": resolution_note[:1024]},
        )

    async def _bump_retry_count(
        self,
        *,
        entry_id: UUID,
        error: BaseException,
    ) -> None:
        await self.db.execute(
            text(
                """
                UPDATE agent_action_dlq
                SET retry_count = retry_count + 1,
                    last_attempted_at = now(),
                    error = :error
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": str(entry_id), "error": str(error)[:8192]},
        )
