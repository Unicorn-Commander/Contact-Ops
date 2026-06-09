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
    "data; if a tool returns nothing, say so. "
    # Qwen honours this to skip its <think> phase — load-bearing on the local
    # 8 k-context model, whose limited output budget otherwise gets spent
    # thinking instead of answering. Other models treat it as inert text.
    "/no_think"
)

# Tool results are fed back into the model's context. A 50-row list with full
# payloads is ~30k tokens — fine for a 200k cloud model, but it overflows a
# locally-served model (e.g. the sovereign Qwen on midboy1) and the proxy 400s.
# Cap each result so the agentic loop stays within a local context window. The
# chat layer additionally slims known large lists (see chat_tools) so the cap
# rarely truncates anything meaningful.
_TOOL_RESULT_CHAR_CAP = 12000


class _ThinkStripper:
    """Strip ``<think>…</think>`` reasoning spans from a streamed text.

    Reasoning ("thinking") models such as Qwen3 emit their chain-of-thought
    wrapped in ``<think>`` tags as ordinary content; without this the raw
    reasoning leaks into the user-facing answer. Tolerates tags split across
    streaming chunks by buffering a possible partial tag at the tail.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._in_think = False
        self._buf = ""

    @staticmethod
    def _tail_partial_len(text: str, tag: str) -> int:
        # Longest suffix of `text` that is a (proper) prefix of `tag`.
        for k in range(min(len(text), len(tag) - 1), 0, -1):
            if tag.startswith(text[-k:]):
                return k
        return 0

    def feed(self, text: str) -> str:
        self._buf += text
        out: list[str] = []
        while self._buf:
            if not self._in_think:
                idx = self._buf.find(self._OPEN)
                if idx == -1:
                    keep = self._tail_partial_len(self._buf, self._OPEN)
                    cut = len(self._buf) - keep
                    out.append(self._buf[:cut])
                    self._buf = self._buf[cut:]
                    break
                out.append(self._buf[:idx])
                self._buf = self._buf[idx + len(self._OPEN):]
                self._in_think = True
            else:
                idx = self._buf.find(self._CLOSE)
                if idx == -1:
                    keep = self._tail_partial_len(self._buf, self._CLOSE)
                    self._buf = self._buf[len(self._buf) - keep:]
                    break
                self._buf = self._buf[idx + len(self._CLOSE):]
                self._in_think = False
        return "".join(out)

    def flush(self) -> str:
        if self._in_think:
            self._buf = ""
            return ""
        out, self._buf = self._buf, ""
        return out

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
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "tools": list(tools),
            "tool_choice": "auto",
            "stream": True,
        }
        # Reasoning models (Qwen3) spend their (here only 8 k) budget on a
        # <think> phase and run out before answering. Disable it at the template
        # level — far more reliable than the "/no_think" soft switch, which Qwen
        # ignores on tool-result turns. Scoped to qwen so cloud models (which
        # reject the unknown param) are untouched.
        if "qwen" in self.model.lower():
            payload["chat_template_kwargs"] = {"enable_thinking": False}
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
            stripper = _ThinkStripper()
            async for chunk in self.llm_client.stream_chat_completion(llm_messages, tools):
                for event in _consume_chunk(chunk, turn, stripper):
                    yield event
            tail = stripper.flush()
            if tail:
                turn.content.append(tail)
                yield {"event": "delta", "content": tail}

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
                serialized = json.dumps(result, sort_keys=True)
                if len(serialized) > _TOOL_RESULT_CHAR_CAP:
                    serialized = (
                        serialized[:_TOOL_RESULT_CHAR_CAP]
                        + f" …[truncated; {len(serialized)} chars total. "
                        + "Ask for a narrower query to see more.]"
                    )
                llm_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "name": call.name,
                        "content": serialized,
                    }
                )


def _compose_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    composed: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for message in messages:
        composed.append({"role": message.role, "content": message.content})
    return composed


def _consume_chunk(
    chunk: dict[str, Any], turn: _AssistantTurn, stripper: _ThinkStripper
) -> list[dict[str, Any]]:
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
            visible = stripper.feed(content)
            if visible:
                turn.content.append(visible)
                events.append({"event": "delta", "content": visible})
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
