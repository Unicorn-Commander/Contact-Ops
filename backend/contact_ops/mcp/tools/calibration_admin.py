"""MCP admin tools for the Phase 3.4 Calibration Daemon.

Three tools, all ADMIN-role gated:

* ``run_calibration_now`` — force a daemon pass (out-of-band of the
  03:00 UTC beat schedule). Useful for verifying a deploy.
* ``apply_tier_promotion`` — the inbox's approval handler calls this
  when Aaron approves a ``calibration.tier_promote`` action_event;
  bumps ``agent_trust.current_tier`` on the target row.
* ``list_calibration_runs`` — read-only history for the ops dashboard.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from contact_ops.agents.calibration import (
    apply_promotion,
    run_calibration_pass,
)
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register


class RunCalibrationNowInput(BaseModel):
    confirm: bool = Field(default=False)


class RunCalibrationNowOutput(ToolOutput):
    daemon_run_id: uuid.UUID
    posteriors_updated: int
    drifts_evaluated: int
    tier_promotes_proposed: int
    tier_demotes_applied: int
    fleet_revert_rate_pct: float | None
    elapsed_seconds: float


async def _handle_run_now(
    ctx: MCPContext,
    payload: RunCalibrationNowInput,
) -> RunCalibrationNowOutput:
    """Force a daemon pass right now (ADMIN-only)."""
    result = await run_calibration_pass(db=ctx.db, audit_db=ctx.audit_db)
    return RunCalibrationNowOutput(
        daemon_run_id=result.daemon_run_id,
        posteriors_updated=result.posteriors_updated,
        drifts_evaluated=result.drifts_evaluated,
        tier_promotes_proposed=result.tier_promotes_proposed,
        tier_demotes_applied=result.tier_demotes_applied,
        fleet_revert_rate_pct=result.fleet_revert_rate_pct,
        elapsed_seconds=(result.ended_at - result.started_at).total_seconds(),
    )


class ApplyTierPromotionInput(BaseModel):
    proposal_event_id: uuid.UUID


class ApplyTierPromotionOutput(ToolOutput):
    applied: bool


async def _handle_apply_promotion(
    ctx: MCPContext,
    payload: ApplyTierPromotionInput,
) -> ApplyTierPromotionOutput:
    """Effect a ``calibration.tier_promote`` proposal after inbox approval."""
    await apply_promotion(
        db=ctx.db,
        audit_db=ctx.audit_db,
        proposal_event_id=payload.proposal_event_id,
    )
    return ApplyTierPromotionOutput(applied=True)


class ListCalibrationRunsInput(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class CalibrationRunSummary(BaseModel):
    id: uuid.UUID
    started_at: Any
    ended_at: Any
    posteriors_updated: int
    drifts_evaluated: int
    tier_promotes_proposed: int
    tier_demotes_applied: int
    fleet_revert_rate_pct: float | None


class ListCalibrationRunsOutput(ToolOutput):
    runs: list[CalibrationRunSummary]


async def _handle_list_runs(
    ctx: MCPContext,
    payload: ListCalibrationRunsInput,
) -> ListCalibrationRunsOutput:
    """Recent calibration runs ordered newest first."""
    result = await ctx.db.execute(
        text(
            """
            SELECT id, started_at, ended_at,
                   posteriors_updated, drifts_evaluated,
                   tier_promotes_proposed, tier_demotes_applied,
                   fleet_revert_rate_pct
            FROM calibration_run_log
            ORDER BY started_at DESC
            LIMIT :limit
            """
        ),
        {"limit": payload.limit},
    )
    runs = [
        CalibrationRunSummary(
            id=uuid.UUID(str(row["id"])),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            posteriors_updated=int(row["posteriors_updated"]),
            drifts_evaluated=int(row["drifts_evaluated"]),
            tier_promotes_proposed=int(row["tier_promotes_proposed"]),
            tier_demotes_applied=int(row["tier_demotes_applied"]),
            fleet_revert_rate_pct=(
                float(row["fleet_revert_rate_pct"])
                if row["fleet_revert_rate_pct"] is not None
                else None
            ),
        )
        for row in result.mappings().all()
    ]
    return ListCalibrationRunsOutput(runs=runs)


def register_calibration_admin_tools() -> None:
    register(
        name="run_calibration_now",
        description="Force a CalibrationDaemon pass right now.",
        input_model=RunCalibrationNowInput,
        output_model=RunCalibrationNowOutput,
        handler=_handle_run_now,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="idempotent",
    )
    register(
        name="apply_tier_promotion",
        description=(
            "Effect an approved calibration.tier_promote proposal by bumping "
            "agent_trust.current_tier. Called by the inbox approve handler."
        ),
        input_model=ApplyTierPromotionInput,
        output_model=ApplyTierPromotionOutput,
        handler=_handle_apply_promotion,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="idempotent",
    )
    register(
        name="list_calibration_runs",
        description="History of CalibrationDaemon passes.",
        input_model=ListCalibrationRunsInput,
        output_model=ListCalibrationRunsOutput,
        handler=_handle_list_runs,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="repeatable",
    )


register_calibration_admin_tools()
