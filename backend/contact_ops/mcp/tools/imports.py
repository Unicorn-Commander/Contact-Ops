"""Import job MCP tools."""
# ruff: noqa: I001

from __future__ import annotations

import asyncio
import uuid
from typing import cast

import sqlalchemy as sa
from pydantic import BaseModel, Field, model_validator

from contact_ops.importers.base import ProvenanceContext, SourceKind
from contact_ops.importers.google_csv import GoogleCSVImporter
from contact_ops.importers.icloud_carddav import ICloudCardDAVImporter
from contact_ops.importers.job_runner import (
    _TASKS,
    cancel_job,
    create_import_job,
    get_job_status,
    import_jobs_table,
)
from contact_ops.importers.linkedin_csv import LinkedInCSVImporter
from contact_ops.importers.mcp_writer import MCPImporterWriter
from contact_ops.importers.nextcloud_carddav import NextcloudCardDAVImporter
from contact_ops.importers.vcard import VCardImporter
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.tools.common import ToolOutput, register


class ImportContactsInput(BaseModel):
    source: SourceKind
    file_uri: str | None = None
    url: str | None = None
    tenant_id: uuid.UUID
    dedup_threshold: float = Field(default=0.85, ge=0, le=1)
    dry_run: bool = True
    batch_size: int = Field(default=50, ge=1, le=500)

    @model_validator(mode="after")
    def validate_location(self) -> ImportContactsInput:
        if self.source in {"vcard", "google_csv", "linkedin_csv"} and not self.file_uri:
            raise ValueError("file_uri is required for file-based imports")
        return self


class ImportContactsOutput(ToolOutput):
    job_id: uuid.UUID
    estimated_records: int | None = None


class ImportJobStatusInput(BaseModel):
    job_id: uuid.UUID


class ImportJobStatusOutput(ToolOutput):
    status: str
    progress: float
    stats: dict[str, int]
    started_at: str | None = None
    completed_at: str | None = None
    error_log: list[str]


class CancelImportJobOutput(ToolOutput):
    job_id: uuid.UUID
    cancelled: bool


async def import_contacts(ctx: MCPContext, req: ImportContactsInput) -> ImportContactsOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write", "person:bulk"))
    if req.tenant_id != ctx.tenant_id:
        raise ToolError(VALIDATION_FAILED, "tenant_id must match caller tenant")
    importer = _importer(req)
    estimated = None
    if req.file_uri:
        estimated = len(await importer.records())
    handle = await create_import_job(
        ctx,
        source=req.source,
        source_uri=importer.source_uri,
        estimated_records=estimated,
    )
    _TASKS[handle.job_id] = asyncio.create_task(
        _run_import_job(
            req=req,
            job_id=handle.job_id,
            user_id=ctx.user_id,
            actor_chain=ctx.actor_chain,
            human_authority=ctx.human_authority,
            request_id=ctx.request_id,
            claims=ctx.claims,
        )
    )
    return ImportContactsOutput(job_id=handle.job_id, estimated_records=handle.estimated_records)


async def get_import_job_status(
    ctx: MCPContext, req: ImportJobStatusInput
) -> ImportJobStatusOutput:
    require_role(ctx, "STAFF")
    status = await get_job_status(ctx, req.job_id)
    if status is None:
        raise ToolError(VALIDATION_FAILED, "import job not found")
    return ImportJobStatusOutput(
        status=status.status,
        progress=status.progress,
        stats=status.stats,
        started_at=status.started_at.isoformat() if status.started_at else None,
        completed_at=status.completed_at.isoformat() if status.completed_at else None,
        error_log=status.error_log,
    )


async def cancel_import_job(ctx: MCPContext, req: ImportJobStatusInput) -> CancelImportJobOutput:
    require_role(ctx, "STAFF")
    return CancelImportJobOutput(job_id=req.job_id, cancelled=await cancel_job(ctx, req.job_id))


def _importer(req: ImportContactsInput):
    if req.source == "vcard":
        return VCardImporter(path=cast(str, req.file_uri), batch_size=req.batch_size)
    if req.source == "google_csv":
        return GoogleCSVImporter(path=cast(str, req.file_uri), batch_size=req.batch_size)
    if req.source == "linkedin_csv":
        return LinkedInCSVImporter(path=cast(str, req.file_uri), batch_size=req.batch_size)
    if req.source == "nextcloud":
        return NextcloudCardDAVImporter(url=req.url, batch_size=req.batch_size)
    return ICloudCardDAVImporter(url=req.url, batch_size=req.batch_size)


async def _run_import_job(
    *,
    req: ImportContactsInput,
    job_id: uuid.UUID,
    user_id: str,
    actor_chain: dict[str, object],
    human_authority: str,
    request_id: str,
    claims: dict[str, object],
) -> None:
    from contact_ops.core.config import get_settings
    from contact_ops.core.database import (
        async_session_maker,
        audit_session_maker,
        bind_session_context,
    )

    settings = get_settings()
    # uc_uid source: claims["uc_uid"] (normalized at JWT validation), with the
    # background-job's user_id as the fallback for the import worker.
    uc_uid = str(claims.get("uc_uid") or user_id)
    async with async_session_maker() as db, audit_session_maker() as audit_db:
        await bind_session_context(db, str(req.tenant_id), uc_uid, settings)
        await bind_session_context(audit_db, str(req.tenant_id), uc_uid, settings)
        await db.execute(
            import_jobs_table.update()
            .where(import_jobs_table.c.id == job_id)
            .values(status="running", progress=0)
        )
        await db.commit()
        # P0: `SET LOCAL app.tenant_id` is transaction-scoped — the status commit
        # above ended the transaction and CLEARED the RLS GUC. Without re-binding,
        # every importer read/write below runs with NO tenant context under
        # enforced RLS (rows invisible / WITH-CHECK violations). Re-bind both
        # sessions before the importer touches data.
        await bind_session_context(db, str(req.tenant_id), uc_uid, settings)
        await bind_session_context(audit_db, str(req.tenant_id), uc_uid, settings)
        try:
            importer = _importer(req)
            records = await importer.records()
            ctx = MCPContext(
                tenant_id=req.tenant_id,
                user_id=user_id,
                actor_chain=actor_chain,
                human_authority=human_authority,
                db=db,
                audit_db=audit_db,
                request_id=request_id,
                claims=claims,
            )
            writer = MCPImporterWriter(
                ctx=ctx,
                provenance=ProvenanceContext(
                    source_kind=req.source,
                    source_uri=importer.source_uri,
                    tenant_id=req.tenant_id,
                ),
                dry_run=req.dry_run,
                dedup_threshold=req.dedup_threshold,
            )
            result = await writer.write_records(records)
            stats = {
                "created": result.stats.created,
                "merged": result.stats.merged,
                "candidates": result.stats.candidates_logged,
                "errors": result.stats.errors,
            }
            await db.execute(
                import_jobs_table.update()
                .where(import_jobs_table.c.id == job_id)
                .values(
                    status="completed" if result.stats.errors == 0 else "failed",
                    progress=1,
                    stats=stats,
                    error_log=result.errors,
                    completed_at=sa.func.now(),
                )
            )
            await db.commit()
            await audit_db.commit()
        except asyncio.CancelledError:
            await db.rollback()
            await audit_db.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - job status must preserve failure detail.
            await db.rollback()
            await audit_db.rollback()
            # The rollbacks above cleared SET LOCAL app.tenant_id (txn-scoped).
            # import_jobs has FORCE ROW LEVEL SECURITY, so the failed-status
            # UPDATE would match 0 rows under a non-privileged role (the job
            # would be stuck 'running'). Re-bind before writing the failure.
            await bind_session_context(db, str(req.tenant_id), uc_uid, settings)
            await bind_session_context(audit_db, str(req.tenant_id), uc_uid, settings)
            await db.execute(
                import_jobs_table.update()
                .where(import_jobs_table.c.id == job_id)
                .values(status="failed", error_log=[str(exc)], completed_at=sa.func.now())
            )
            await db.commit()


for _name, _input, _output, _handler, _read_only in [
    ("import_contacts", ImportContactsInput, ImportContactsOutput, import_contacts, False),
    (
        "get_import_job_status",
        ImportJobStatusInput,
        ImportJobStatusOutput,
        get_import_job_status,
        True,
    ),
    ("cancel_import_job", ImportJobStatusInput, CancelImportJobOutput, cancel_import_job, False),
]:
    register(
        name=_name,
        description=f"{_name.replace('_', ' ')} for background contact imports.",
        input_model=cast(type[BaseModel], _input),
        output_model=cast(type[BaseModel], _output),
        handler=_handler,
        required_role="STAFF",
        required_scopes=("person:write", "person:bulk") if _name == "import_contacts" else (),
        annotations={
            "readOnlyHint": bool(_read_only),
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
