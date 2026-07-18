"""Chat orchestrator loop is bounded (hard ceiling + CostGuard turn budget)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from contact_ops.agents.cost_guard import CostBudget, CostGuard
from contact_ops.services.chat_orchestrator import (
    _MAX_TOOL_CALL_TURNS,
    ChatMessage,
    ChatOrchestrator,
)
from contact_ops.services.chat_tools import ChatRequestContext


class _AlwaysToolCallClient:
    """A model that never stops: every turn requests a tool call."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat_completion(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls += 1
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"call_{self.calls}",
                                "type": "function",
                                "function": {"name": "list_people", "arguments": "{}"},
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }


class _StubToolExecutor:
    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_people",
                    "description": "List people",
                    "parameters": {"type": "object"},
                },
            }
        ]

    async def execute(
        self, name: str, args: dict[str, Any], ctx: ChatRequestContext
    ) -> dict[str, Any]:
        return {"ok": True}


class _FakeResult:
    def scalar_one(self) -> int:
        return 0


class _FakeGuardDB:
    """Stands in for an AsyncSession — CostGuard only needs execute()->scalar_one."""

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult()


def _ctx() -> ChatRequestContext:
    return ChatRequestContext(
        tenant_id=uuid.uuid4(),
        user_id="user-1",
        actor_chain={"sub": "user-1"},
        human_authority="user-1",
        claims={"sub": "user-1"},
        request_id="req-1",
    )


async def _collect(agen: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event async for event in agen]


@pytest.mark.asyncio
async def test_loop_stops_at_hard_ceiling_without_cost_guard() -> None:
    orchestrator = ChatOrchestrator(
        llm_client=_AlwaysToolCallClient(), tool_executor=_StubToolExecutor()
    )
    events = await _collect(
        orchestrator.stream(
            messages=[ChatMessage(role="user", content="loop forever")],
            conversation_id=None,
            ctx=_ctx(),
            cost_guard=None,
        )
    )
    tool_calls = [e for e in events if e.get("event") == "tool_call"]
    errors = [e for e in events if e.get("event") == "error"]
    # Exactly the ceiling number of turns ran, then a single error + done.
    assert len(tool_calls) == _MAX_TOOL_CALL_TURNS
    assert errors and "maximum" in errors[-1]["message"]
    assert events[-1]["event"] == "done"


@pytest.mark.asyncio
async def test_cost_guard_turn_budget_stops_loop_before_ceiling() -> None:
    orchestrator = ChatOrchestrator(
        llm_client=_AlwaysToolCallClient(), tool_executor=_StubToolExecutor()
    )
    guard = CostGuard(db=_FakeGuardDB(), budget=CostBudget(max_turns_per_run=3))
    events = await _collect(
        orchestrator.stream(
            messages=[ChatMessage(role="user", content="loop forever")],
            conversation_id=None,
            ctx=_ctx(),
            cost_guard=guard,
        )
    )
    tool_calls = [e for e in events if e.get("event") == "tool_call"]
    errors = [e for e in events if e.get("event") == "error"]
    # The layer-2 turn counter (3) trips well before the hard ceiling (50).
    assert len(tool_calls) == 3
    assert errors and "budget" in errors[-1]["message"].lower()
    assert events[-1]["event"] == "done"


@pytest.mark.asyncio
async def test_cost_guard_db_error_fails_open_to_hard_ceiling() -> None:
    """A fully unavailable DB must not crash chat — the hard ceiling is the net.

    When the CostGuard's session raises, even recording a violation fails, so the
    guard cannot enforce. ``_enforce_budget`` fails open (never 500s the stream)
    and the guard-independent hard ceiling is what finally bounds the loop. This
    is exactly why the ceiling exists alongside the budget.
    """

    class _BrokenDB:
        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("db down")

    orchestrator = ChatOrchestrator(
        llm_client=_AlwaysToolCallClient(), tool_executor=_StubToolExecutor()
    )
    guard = CostGuard(db=_BrokenDB(), budget=CostBudget(max_turns_per_run=3))
    events = await _collect(
        orchestrator.stream(
            messages=[ChatMessage(role="user", content="loop forever")],
            conversation_id=None,
            ctx=_ctx(),
            cost_guard=guard,
        )
    )
    tool_calls = [e for e in events if e.get("event") == "tool_call"]
    assert len(tool_calls) == _MAX_TOOL_CALL_TURNS
    assert events[-1]["event"] == "done"
