"""MCP tool for propose-only CSV imports (Google Contacts / LinkedIn).

Agent-facing twin of the GUI's POST /import/csv, so an MCP client (Claude Code,
Brigade agent, …) can import contacts the same way a human does in the app. An
agent that can read a user's contacts can export them and propose them here;
nothing is auto-applied — every record lands in the Review Queue.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import register
from contact_ops.services.csv_import import CSVImportError, propose_csv_import
from contact_ops.services.import_propose import ImportSummary


class ProposeCSVRecordsInput(BaseModel):
    csv_text: str = Field(min_length=1, max_length=50 * 1024 * 1024)
    dry_run: bool = False
    # Trust this import → auto-approve high-confidence rows (they land + graph
    # immediately) instead of waiting in the Review Queue. Duplicates are still
    # skipped; only genuinely new, reversible person.create rows auto-apply.
    auto_approve: bool = False


async def propose_csv_records(
    ctx: MCPContext,
    req: ProposeCSVRecordsInput,
) -> ImportSummary:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("person:write", "person:bulk"))
    try:
        return await propose_csv_import(
            ctx=ctx,
            csv_text=req.csv_text,
            dry_run=req.dry_run,
            auto_approve=req.auto_approve,
            filename="mcp-pasted.csv",
        )
    except CSVImportError as exc:
        from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError

        raise ToolError(VALIDATION_FAILED, str(exc), retryable=False) from exc


register(
    name="propose_csv_records",
    description=(
        "Parse a contacts CSV (Google Contacts export or LinkedIn Connections.csv) "
        "and create Review Queue proposals for each contact. Auto-detects the "
        "format from the header. Propose-only: never inserts or auto-applies "
        "Person records, and contacts already in this tenant are skipped as "
        "duplicates rather than re-proposed."
    ),
    input_model=ProposeCSVRecordsInput,
    output_model=ImportSummary,
    handler=propose_csv_records,
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
