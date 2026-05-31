"""Connector base classes and shared orchestration."""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from cryptography.fernet import Fernet
from pydantic import BaseModel, Field
from sqlalchemy import text

from contact_ops.core.config import get_settings
from contact_ops.core.database import async_session_maker, bind_session_context
from contact_ops.importers.base import CanonicalImportRecord
from contact_ops.services.agents.confidence_approver import run_confidence_approver
from contact_ops.services.agents.dedup_agent import run_dedup_agent
from contact_ops.services.agents.quality_filter import run_quality_filter
from contact_ops.services.proposal_emit import emit_person_create_proposal, import_fingerprint

if TYPE_CHECKING:
    from contact_ops.mcp.registry import MCPContext

Provider = Literal["icloud", "m365", "gmail"]


class ConnectorRunResult(BaseModel):
    parsed_count: int = 0
    proposed_count: int = 0
    deduped_count: int = 0
    skipped_count: int = 0
    errors: list[str] = Field(default_factory=list)


class Connector(ABC):
    provider: Provider

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    @abstractmethod
    async def pull(self, ctx: MCPContext, since: datetime | None) -> list[CanonicalImportRecord]:
        """Return canonical records from the upstream provider."""


def encrypt_payload(payload: dict[str, Any]) -> bytes:
    return _fernet().encrypt(json.dumps(payload, sort_keys=True, default=str).encode())


def decrypt_payload(value: bytes) -> dict[str, Any]:
    decoded = json.loads(_fernet().decrypt(value).decode())
    if not isinstance(decoded, dict):
        raise ValueError("connector payload is not an object")
    return decoded


async def run_connector_pull(
    *,
    ctx: MCPContext,
    connector_id: uuid.UUID,
    connector: Connector,
    dry_run: bool,
    triggered_by: Literal["user", "schedule", "agent"] = "user",
) -> tuple[uuid.UUID, ConnectorRunResult]:
    run_id = await _start_run(ctx, connector_id, triggered_by)
    result = ConnectorRunResult()
    try:
        records = await connector.pull(ctx, since=None)
        seen: set[str] = set()
        upload_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"connector:{connector_id}:{datetime.now(UTC).date()}",
        )
        for index, record in enumerate(records):
            result.parsed_count += 1
            fingerprint = import_fingerprint(record)
            if fingerprint in seen:
                result.deduped_count += 1
                continue
            seen.add(fingerprint)
            if dry_run:
                continue
            await emit_person_create_proposal(
                ctx=ctx,
                record=record,
                upload_id=upload_id,
                source={
                    "action": "connector_pull",
                    "provider": connector.provider,
                    "connector_id": str(connector_id),
                    "connector_run_id": str(run_id),
                    "record_index": index,
                    "source_record_id": record.source_record_id,
                },
            )
            result.proposed_count += 1
        await _finish_run(ctx, connector_id, run_id, result, None)
    except Exception as exc:
        result.errors.append(str(exc))
        await _finish_run(ctx, connector_id, run_id, result, str(exc))
        raise
    return run_id, result


async def _start_run(
    ctx: MCPContext,
    connector_id: uuid.UUID,
    triggered_by: str,
) -> uuid.UUID:
    row = await ctx.db.execute(
        text(
            """
            INSERT INTO connector_runs (tenant_id, connector_id, triggered_by)
            VALUES (CAST(:tenant_id AS uuid), CAST(:connector_id AS uuid), :triggered_by)
            RETURNING id
            """
        ),
        {
            "tenant_id": str(ctx.tenant_id),
            "connector_id": str(connector_id),
            "triggered_by": triggered_by,
        },
    )
    return uuid.UUID(str(row.scalar_one()))


async def _finish_run(
    ctx: MCPContext,
    connector_id: uuid.UUID,
    run_id: uuid.UUID,
    result: ConnectorRunResult,
    error_message: str | None,
) -> None:
    status = "failed" if error_message else ("partial" if result.errors else "succeeded")
    summary = {
        "parsed": result.parsed_count,
        "proposed": result.proposed_count,
        "deduped": result.deduped_count,
        "skipped": result.skipped_count,
        "errors_count": len(result.errors),
    }
    await ctx.db.execute(
        text(
            """
            UPDATE connector_runs
            SET completed_at = now(), status = :status,
                parsed_count = :parsed, proposed_count = :proposed,
                deduped_count = :deduped, skipped_count = :skipped,
                error_message = CAST(:error_message AS text)
            WHERE id = CAST(:run_id AS uuid)
              AND tenant_id = CAST(:tenant_id AS uuid)
            """
        ),
        {
            "run_id": str(run_id),
            "tenant_id": str(ctx.tenant_id),
            "status": status,
            "parsed": result.parsed_count,
            "proposed": result.proposed_count,
            "deduped": result.deduped_count,
            "skipped": result.skipped_count,
            "error_message": error_message,
        },
    )
    await ctx.db.execute(
        text(
            """
            UPDATE connector_configs
            SET last_pull_at = now(),
                last_pull_summary = CAST(:summary AS jsonb),
                last_error = CAST(:error_message AS text),
                status = CASE
                    WHEN CAST(:error_message AS text) IS NULL THEN 'configured'
                    ELSE 'error'
                END
            WHERE id = CAST(:connector_id AS uuid)
              AND tenant_id = CAST(:tenant_id AS uuid)
            """
        ),
        {
            "connector_id": str(connector_id),
            "tenant_id": str(ctx.tenant_id),
            "summary": json.dumps(summary),
            "error_message": error_message,
        },
    )
    if status == "succeeded" and result.proposed_count > 0:
        asyncio.create_task(_run_post_pull_agent_pipeline(ctx.tenant_id))


async def _run_post_pull_agent_pipeline(tenant_id: uuid.UUID) -> None:
    # Let the request transaction commit before the independent worker session reads proposals.
    await asyncio.sleep(0.1)
    settings = get_settings()
    # Pure background worker (post-pull agent pipeline) — no human uc_uid in
    # scope. Bind a stable service identity so attribution is never null.
    async with async_session_maker() as db:
        await bind_session_context(
            db, str(tenant_id), "service:connector-pull-pipeline", settings
        )
        try:
            await run_dedup_agent(db, tenant_id=tenant_id)
            await run_quality_filter(db, tenant_id=tenant_id)
            await run_confidence_approver(db, tenant_id=tenant_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise


def _fernet() -> Fernet:
    key = get_settings().CONNECTOR_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("CONNECTOR_ENCRYPTION_KEY is required for connector encryption")
    return Fernet(key.encode())
