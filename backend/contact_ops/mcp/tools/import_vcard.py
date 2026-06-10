"""MCP tool for propose-only vCard imports."""

from __future__ import annotations

from pydantic import BaseModel, Field

from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import register
from contact_ops.services.vcard_import import (
    VCardImportError,
    VCardImportResult,
    propose_vcard_import,
)


class ProposeVCardRecordsInput(BaseModel):
    vcard_text: str = Field(min_length=1, max_length=50 * 1024 * 1024)
    dry_run: bool = False
    # Trust this import → auto-approve high-confidence rows (land + graph now)
    # instead of queuing them for review. Duplicates are still skipped.
    auto_approve: bool = False


async def propose_vcard_records(
    ctx: MCPContext,
    req: ProposeVCardRecordsInput,
) -> VCardImportResult:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write", "person:bulk"))
    try:
        return await propose_vcard_import(
            ctx=ctx,
            vcard_text=req.vcard_text,
            dry_run=req.dry_run,
            auto_approve=req.auto_approve,
            filename="mcp-pasted.vcf",
        )
    except VCardImportError as exc:
        from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError

        raise ToolError(VALIDATION_FAILED, str(exc), retryable=False) from exc


register(
    name="propose_vcard_records",
    description=(
        "Parse raw vCard text and create Review Queue proposals for each contact. "
        "Propose-only: never inserts or auto-applies Person records."
    ),
    input_model=ProposeVCardRecordsInput,
    output_model=VCardImportResult,
    handler=propose_vcard_records,
    required_role="STAFF",
    required_scopes=("person:write", "person:bulk"),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    idempotency="upload-id",
)
