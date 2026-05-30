from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel

from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register
from contact_ops.services.agents.confidence_approver import run_confidence_approver
from contact_ops.services.agents.dedup_agent import run_dedup_agent
from contact_ops.services.agents.quality_filter import run_quality_filter
from contact_ops.services.brigade_registration import registration_status


class RunDedupAgentNowInput(BaseModel):
    tenant_id: uuid.UUID | None = None
    since: datetime | None = None


class AgentRunOutput(ToolOutput):
    result: dict[str, Any]


class RunConfidenceApproverNowInput(BaseModel):
    tenant_id: uuid.UUID | None = None
    dry_run: bool = False


class RunQualityFilterNowInput(BaseModel):
    tenant_id: uuid.UUID | None = None
    dry_run: bool = False


class GetBrigadeRegistrationStatusInput(BaseModel):
    pass


class GetBrigadeRegistrationStatusOutput(ToolOutput):
    last_successful_registration_at: str | None
    last_response: dict[str, Any] | None
    last_error: str | None


async def run_dedup_agent_now(ctx: MCPContext, req: RunDedupAgentNowInput) -> AgentRunOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("dedup:apply",))
    tenant_id = req.tenant_id or ctx.tenant_id
    result = await run_dedup_agent(ctx.db, tenant_id=tenant_id, since=req.since)
    return AgentRunOutput(result=result)


async def run_confidence_approver_now(
    ctx: MCPContext,
    req: RunConfidenceApproverNowInput,
) -> AgentRunOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("proposal:auto-apply",))
    tenant_id = req.tenant_id or ctx.tenant_id
    return AgentRunOutput(
        result=await run_confidence_approver(ctx.db, tenant_id=tenant_id, dry_run=req.dry_run)
    )


async def run_quality_filter_now(ctx: MCPContext, req: RunQualityFilterNowInput) -> AgentRunOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("proposal:archive",))
    tenant_id = req.tenant_id or ctx.tenant_id
    result = await run_quality_filter(ctx.db, tenant_id=tenant_id, dry_run=req.dry_run)
    return AgentRunOutput(result=result)


async def get_brigade_registration_status(
    ctx: MCPContext,
    _req: GetBrigadeRegistrationStatusInput,
) -> GetBrigadeRegistrationStatusOutput:
    require_role(ctx, "CLIENT")
    return GetBrigadeRegistrationStatusOutput(**registration_status())


for _name, _input, _output, _handler, _scopes, _read_only in [
    (
        "run_dedup_agent_now",
        RunDedupAgentNowInput,
        AgentRunOutput,
        run_dedup_agent_now,
        ("dedup:apply",),
        False,
    ),
    (
        "run_confidence_approver_now",
        RunConfidenceApproverNowInput,
        AgentRunOutput,
        run_confidence_approver_now,
        ("proposal:auto-apply",),
        False,
    ),
    (
        "run_quality_filter_now",
        RunQualityFilterNowInput,
        AgentRunOutput,
        run_quality_filter_now,
        ("proposal:archive",),
        False,
    ),
    (
        "get_brigade_registration_status",
        GetBrigadeRegistrationStatusInput,
        GetBrigadeRegistrationStatusOutput,
        get_brigade_registration_status,
        (),
        True,
    ),
]:
    register(
        name=_name,
        description=f"{_name.replace('_', ' ')}.",
        input_model=_input,
        output_model=cast(type[BaseModel], _output),
        handler=cast(Any, _handler),
        required_role="STAFF" if _scopes else "CLIENT",
        required_scopes=_scopes,
        annotations={
            "readOnlyHint": bool(_read_only),
            "destructiveHint": not _read_only,
            "idempotentHint": _read_only,
            "openWorldHint": False,
        },
        idempotency="none",
    )
