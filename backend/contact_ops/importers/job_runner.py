"""Small DB-backed import job runner."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, Field

from contact_ops.importers.base import SourceKind, utcnow
from contact_ops.mcp.registry import MCPContext

metadata = sa.MetaData()
import_jobs_table = sa.Table(
    "import_jobs",
    metadata,
    sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True)),
    sa.Column("source", sa.Text),
    sa.Column("source_uri", sa.Text),
    sa.Column("status", sa.Text),
    sa.Column("progress", sa.Numeric(5, 4)),
    sa.Column("stats", sa.dialects.postgresql.JSONB),
    sa.Column("error_log", sa.dialects.postgresql.JSONB),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
)


class ImportJobStatus(BaseModel):
    job_id: uuid.UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: float = Field(ge=0, le=1)
    stats: dict[str, int]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_log: list[str]


@dataclass(slots=True)
class ImportJobHandle:
    job_id: uuid.UUID
    estimated_records: int | None


_TASKS: dict[uuid.UUID, asyncio.Task[None]] = {}


async def create_import_job(
    ctx: MCPContext,
    *,
    source: SourceKind,
    source_uri: str,
    estimated_records: int | None = None,
) -> ImportJobHandle:
    job_id = uuid.uuid4()
    await ctx.db.execute(
        import_jobs_table.insert().values(
            id=job_id,
            tenant_id=ctx.tenant_id,
            source=source,
            source_uri=source_uri,
            status="queued",
            progress=0,
            stats={"created": 0, "merged": 0, "candidates": 0, "errors": 0},
            error_log=[],
            started_at=utcnow(),
        )
    )
    return ImportJobHandle(job_id=job_id, estimated_records=estimated_records)


async def get_job_status(ctx: MCPContext, job_id: uuid.UUID) -> ImportJobStatus | None:
    row = (
        await ctx.db.execute(
            sa.select(import_jobs_table).where(
                import_jobs_table.c.id == job_id,
                import_jobs_table.c.tenant_id == ctx.tenant_id,
            )
        )
    ).mappings().first()
    if row is None:
        return None
    stats = dict(cast_dict(row["stats"]))
    return ImportJobStatus(
        job_id=job_id,
        status=row["status"],
        progress=float(row["progress"] or 0),
        stats={key: int(value) for key, value in stats.items()},
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        error_log=list(cast_list(row["error_log"])),
    )


async def cancel_job(ctx: MCPContext, job_id: uuid.UUID) -> bool:
    exists = await ctx.db.scalar(
        sa.select(import_jobs_table.c.id).where(
            import_jobs_table.c.id == job_id,
            import_jobs_table.c.tenant_id == ctx.tenant_id,
        )
    )
    if exists is None:
        return False
    await ctx.db.execute(
        import_jobs_table.update()
        .where(import_jobs_table.c.id == job_id, import_jobs_table.c.tenant_id == ctx.tenant_id)
        .values(status="cancelled", cancel_requested_at=utcnow(), completed_at=utcnow())
    )
    task = _TASKS.get(job_id)
    if task is not None:
        task.cancel()
    return True


def cast_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def cast_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
