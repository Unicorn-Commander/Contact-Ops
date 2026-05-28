from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient

from contact_ops.api import chat as chat_api
from contact_ops.main import app
from contact_ops.services.chat_orchestrator import ChatOrchestrator
from contact_ops.services.chat_tools import ChatRequestContext


class StubLLMClient:
    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat_completion(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls += 1
        if self.calls == 1:
            yield _chunk({"content": "Checking "})
            yield _chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "list_people",
                                "arguments": '{"limit":2}',
                            },
                        }
                    ]
                },
                finish_reason="tool_calls",
            )
            return
        assert messages[-1]["role"] == "tool"
        yield _chunk({"content": "There are 2 people."}, finish_reason="stop")


class StubToolExecutor:
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
        self,
        name: str,
        args: dict[str, Any],
        ctx: ChatRequestContext,
    ) -> dict[str, Any]:
        assert name == "list_people"
        assert args == {"limit": 2}
        assert str(ctx.tenant_id) == "00000000-0000-0000-0000-000000000001"
        return {"items": [{"display_name": "Ada Lovelace"}], "count": 2}


@pytest.fixture(autouse=True)
def _set_standalone() -> Generator[None]:
    from contact_ops.core.config import get_settings

    settings = get_settings()
    previous = settings.STANDALONE_MODE
    settings.STANDALONE_MODE = True
    yield
    settings.STANDALONE_MODE = previous


def test_chat_sse_stream_order_with_tool_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = ChatOrchestrator(
        llm_client=StubLLMClient(),
        tool_executor=StubToolExecutor(),
    )
    monkeypatch.setattr(chat_api, "ChatOrchestrator", lambda: orchestrator)

    client = TestClient(app)
    with client.stream(
        "POST",
        "/chat",
        json={"messages": [{"role": "user", "content": "how many people are here?"}]},
    ) as response:
        assert response.status_code == 200
        events = [_parse_sse(line) for line in response.iter_lines() if line]

    assert [event["event"] for event in events] == [
        "delta",
        "tool_call",
        "tool_result",
        "delta",
        "done",
    ]
    assert events[0]["content"] == "Checking "
    assert events[1] == {
        "event": "tool_call",
        "call_id": "call_abc",
        "tool": "list_people",
        "args": {"limit": 2},
    }
    assert events[2]["result"]["count"] == 2
    assert events[3]["content"] == "There are 2 people."
    assert events[4]["conversation_id"]


def _chunk(delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
    return {"choices": [{"delta": delta, "finish_reason": finish_reason}]}


def _parse_sse(line: str) -> dict[str, Any]:
    assert line.startswith("data: ")
    parsed = json.loads(line.removeprefix("data: "))
    assert isinstance(parsed, dict)
    return parsed
