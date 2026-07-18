"""SSE chat endpoint for the AI Contacts Analyst."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from contact_ops.agents.cost_guard import CostBudget, CostGuard
from contact_ops.core.config import get_settings
from contact_ops.core.database import async_session_maker, bind_session_context
from contact_ops.services.chat_orchestrator import ChatMessage, ChatOrchestrator
from contact_ops.services.chat_tools import ChatRequestContext

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    conversation_id: uuid.UUID | None = None


@router.post("/chat")
async def chat_endpoint(request: Request, body: ChatRequest) -> StreamingResponse:
    claims = getattr(request.state, "jwt_claims", None)
    if not isinstance(claims, dict) or not claims.get("tenant_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="tenant context unresolved",
        )
    try:
        tenant_id = uuid.UUID(str(claims["tenant_id"]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid tenant_id claim",
        ) from exc

    user_id = str(claims.get("sub") or "")
    ctx = ChatRequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_chain=_state_dict(request, "actor_chain", {"sub": user_id}),
        human_authority=str(getattr(request.state, "human_authority", user_id) or user_id),
        claims=claims,
        request_id=str(
            getattr(request.state, "request_id", "")
            or request.headers.get("x-request-id")
            or uuid.uuid4()
        ),
    )
    orchestrator = ChatOrchestrator()
    return StreamingResponse(
        _sse_stream(orchestrator, body, ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse_stream(
    orchestrator: ChatOrchestrator,
    body: ChatRequest,
    ctx: ChatRequestContext,
) -> AsyncIterator[str]:
    # Bring the chat loop under the same CostGuard as the agent fleet. The guard
    # needs an RLS-bound session for the tenant's burn view; open one for the
    # stream's lifetime. If that fails (DB down, self-host without metering) we
    # degrade to no guard — the orchestrator's hard turn ceiling still bounds the
    # loop — rather than fail the request.
    session = None
    cost_guard: CostGuard | None = None
    try:
        session, cost_guard = await _build_cost_guard(ctx)
    except Exception as exc:  # noqa: BLE001 — guard is best-effort, never blocking
        logger.warning(
            "chat_cost_guard_setup_failed", tenant_id=str(ctx.tenant_id), error=str(exc)
        )

    try:
        async for event in orchestrator.stream(
            messages=body.messages,
            conversation_id=body.conversation_id,
            ctx=ctx,
            cost_guard=cost_guard,
        ):
            yield _sse(event)
    except Exception as exc:
        yield _sse({"event": "error", "message": str(exc)})
    finally:
        if session is not None:
            await session.close()


async def _build_cost_guard(
    ctx: ChatRequestContext,
) -> tuple[Any, CostGuard]:
    """Open an RLS-bound session for the tenant and wrap it in a CostGuard.

    Raises if the session cannot be established/bound (caller degrades to no
    guard). The returned session must be closed by the caller.
    """
    settings = get_settings()
    session = async_session_maker()
    try:
        uc_uid = str(ctx.claims.get("uc_uid") or ctx.user_id)
        await bind_session_context(session, str(ctx.tenant_id), uc_uid, settings)
    except Exception:
        await session.close()
        raise
    return session, CostGuard(db=session, budget=CostBudget())


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _state_dict(request: Request, key: str, default: dict[str, Any]) -> dict[str, Any]:
    value = getattr(request.state, key, default)
    if isinstance(value, dict):
        return value
    return default
