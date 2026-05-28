"""Streaming LLM orchestration for Contact-Ops chat."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from contact_ops.services.chat_tools import (
    ChatRequestContext,
    ChatToolExecutor,
    parse_tool_arguments,
)

SYSTEM_PROMPT = (
    "You are the AI Contacts Analyst for Contact-Ops, the canonical contacts/identity "
    "hub for the Magic Unicorn ecosystem. You have read+propose tool access to the "
    "user's tenant. Be precise, cite entities by short name + last-6 of their UUID, "
    "and only ever PROPOSE changes (merges, edits, tags) via the propose-* tools — "
    "propositions land in the Review Queue for the user to approve. Never fabricate "
    "data; if a tool returns nothing, say so."
)

ChatRole = Literal["user", "assistant", "tool"]


class ChatMessage(BaseModel):
    role: ChatRole
    content: str


class ChatCompletionClient(Protocol):
    def stream_chat_completion(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield OpenAI-compatible streaming chunks."""


class ToolExecutor(Protocol):
    def definitions(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool definitions."""

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        ctx: ChatRequestContext,
    ) -> dict[str, Any]:
        """Execute a named tool."""


class OpenAICompatibleClient:
    """Minimal OpenAI-compatible streaming client for LiteLLM."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.model = os.environ.get("LLM_MODEL", "gpt-4.1-mini")

    async def stream_chat_completion(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        if not self.base_url or not self.api_key:
            raise RuntimeError("LLM_BASE_URL and LLM_API_KEY are required")
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "messages": list(messages),
            "tools": list(tools),
            "tool_choice": "auto",
            "stream": True,
        }
        timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    parsed = json.loads(data)
                    if isinstance(parsed, dict):
                        yield parsed


@dataclass
class _ToolCallBuffer:
    index: int
    call_id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class _AssistantTurn:
    content: list[str] = field(default_factory=list)
    tool_calls: dict[int, _ToolCallBuffer] = field(default_factory=dict)
    finish_reason: str | None = None


class ChatOrchestrator:
    def __init__(
        self,
        *,
        llm_client: ChatCompletionClient | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self.llm_client = llm_client or OpenAICompatibleClient()
        self.tool_executor = tool_executor or ChatToolExecutor()

    async def stream(
        self,
        *,
        messages: Sequence[ChatMessage],
        conversation_id: uuid.UUID | None,
        ctx: ChatRequestContext,
    ) -> AsyncIterator[dict[str, Any]]:
        resolved_conversation_id = conversation_id or uuid.uuid4()
        llm_messages = _compose_messages(messages)
        tools = self.tool_executor.definitions()

        while True:
            turn = _AssistantTurn()
            async for chunk in self.llm_client.stream_chat_completion(llm_messages, tools):
                for event in _consume_chunk(chunk, turn):
                    yield event

            if not turn.tool_calls:
                if turn.content:
                    llm_messages.append(
                        {"role": "assistant", "content": "".join(turn.content)}
                    )
                yield {"event": "done", "conversation_id": str(resolved_conversation_id)}
                return

            assistant_tool_calls = [_assistant_tool_call(call) for call in _ordered(turn)]
            llm_messages.append(
                {
                    "role": "assistant",
                    "content": "".join(turn.content) or None,
                    "tool_calls": assistant_tool_calls,
                }
            )
            for call in _ordered(turn):
                try:
                    args = parse_tool_arguments(call.arguments)
                except Exception as exc:
                    args = {}
                    result = {"error": str(exc)}
                else:
                    try:
                        result = await self.tool_executor.execute(call.name, args, ctx)
                    except Exception as exc:
                        result = {"error": str(exc)}
                yield {
                    "event": "tool_call",
                    "call_id": call.call_id,
                    "tool": call.name,
                    "args": args,
                }
                yield {"event": "tool_result", "call_id": call.call_id, "result": result}
                llm_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "name": call.name,
                        "content": json.dumps(result, sort_keys=True),
                    }
                )


def _compose_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    composed: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for message in messages:
        composed.append({"role": message.role, "content": message.content})
    return composed


def _consume_chunk(chunk: dict[str, Any], turn: _AssistantTurn) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return events
    choice = choices[0]
    if not isinstance(choice, dict):
        return events
    delta = choice.get("delta")
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str) and content:
            turn.content.append(content)
            events.append({"event": "delta", "content": content})
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for raw_call in tool_calls:
                if isinstance(raw_call, dict):
                    _merge_tool_call(turn, raw_call)
    finish_reason = choice.get("finish_reason")
    if isinstance(finish_reason, str):
        turn.finish_reason = finish_reason
    return events


def _merge_tool_call(turn: _AssistantTurn, raw_call: dict[str, Any]) -> None:
    index_raw = raw_call.get("index", 0)
    index = index_raw if isinstance(index_raw, int) else 0
    call = turn.tool_calls.setdefault(index, _ToolCallBuffer(index=index))
    call_id = raw_call.get("id")
    if isinstance(call_id, str):
        call.call_id = call_id
    function = raw_call.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str):
            call.name += name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            call.arguments += arguments


def _ordered(turn: _AssistantTurn) -> list[_ToolCallBuffer]:
    return [turn.tool_calls[index] for index in sorted(turn.tool_calls)]


def _assistant_tool_call(call: _ToolCallBuffer) -> dict[str, Any]:
    return {
        "id": call.call_id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments},
    }
