"""MCP tool registry shared by all Phase 1 tool modules."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class MCPContext:
    tenant_id: uuid.UUID
    user_id: str
    actor_chain: dict[str, Any]
    human_authority: str
    db: AsyncSession
    audit_db: AsyncSession
    request_id: str
    claims: dict[str, Any]


ToolHandler = Callable[[MCPContext, BaseModel], Awaitable[BaseModel]]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler
    required_role: str
    required_scopes: tuple[str, ...]
    annotations: dict[str, bool]
    idempotency: str


_REGISTRY: dict[str, ToolDef] = {}


def register_tool(tool: ToolDef) -> None:
    if tool.name in _REGISTRY:
        raise ValueError(f"tool already registered: {tool.name}")
    _REGISTRY[tool.name] = tool


def get_tool(name: str) -> ToolDef | None:
    return _REGISTRY.get(name)


def list_tools() -> list[ToolDef]:
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]
