"""Tool-level action_event emission."""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text

from contact_ops.mcp.errors import VALIDATION_FAILED, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.models import ActionEvent
from contact_ops.models.enums import ActorType, EntityKind, EventStatus


async def emit_action_event(
    ctx: MCPContext,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    affected_ids: list[uuid.UUID] | None = None,
    payload_before: dict[str, Any] | None,
    payload_after: dict[str, Any],
    status: str = "applied",
    confidence: float | None = None,
    rationale: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Write an append-only action_event row via ctx.audit_db."""

    try:
        entity_kind = EntityKind(aggregate_type)
    except ValueError as exc:
        raise ToolError(VALIDATION_FAILED, f"invalid aggregate_type: {aggregate_type}") from exc
    try:
        event_status = EventStatus(status)
    except ValueError as exc:
        raise ToolError(VALIDATION_FAILED, f"invalid status: {status}") from exc

    payload = {"before": payload_before, "after": payload_after}
    payload_bytes = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    prev_hash = await ctx.audit_db.scalar(
        select(ActionEvent.content_hash)
        .where(ActionEvent.tenant_id == ctx.tenant_id)
        .order_by(ActionEvent.proposed_at.desc())
        .limit(1)
    )
    human_authority: uuid.UUID | None
    try:
        human_authority = uuid.UUID(ctx.human_authority)
    except (TypeError, ValueError):
        human_authority = None

    actor_type = ActorType.agent if ctx.actor_chain.get("act") else ActorType.human
    event = ActionEvent(
        tenant_id=ctx.tenant_id,
        event_type=event_type,
        aggregate_type=entity_kind,
        aggregate_id=aggregate_id,
        affected_ids=affected_ids or [],
        payload=payload,
        actor=ctx.actor_chain,
        actor_type=actor_type,
        human_authority=human_authority,
        confidence=(
            Decimal(str(confidence))
            if confidence is not None and actor_type != ActorType.human
            else None
        ),
        evidence=evidence or {"request_id": ctx.request_id},
        rationale=rationale,
        status=event_status,
        content_hash=hashlib.sha256(payload_bytes).digest(),
        prev_event_hash=prev_hash,
    )
    ctx.audit_db.add(event)
    await ctx.audit_db.flush()
    await ctx.audit_db.execute(text("SELECT 1"))
    return event.event_id
