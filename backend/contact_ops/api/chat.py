"""SSE chat endpoint for the AI Contacts Analyst."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from contact_ops.services.chat_orchestrator import ChatMessage, ChatOrchestrator
from contact_ops.services.chat_tools import ChatRequestContext

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
    try:
        async for event in orchestrator.stream(
            messages=body.messages,
            conversation_id=body.conversation_id,
            ctx=ctx,
        ):
            yield _sse(event)
    except Exception as exc:
        yield _sse({"event": "error", "message": str(exc)})


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _state_dict(request: Request, key: str, default: dict[str, Any]) -> dict[str, Any]:
    value = getattr(request.state, key, default)
    if isinstance(value, dict):
        return value
    return default
