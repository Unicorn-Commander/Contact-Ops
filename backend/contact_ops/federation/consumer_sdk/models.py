"""Shared Pydantic shapes for Contact-Ops consumer apps."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JsonRpcError(RuntimeError):
    """Raised when Contact-Ops MCP returns a JSON-RPC error payload."""

    def __init__(self, code: int | str, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data or {}


class PersonSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    person_id: uuid.UUID
    display_name: str
    primary_email: str | None = None
    current_org_id: uuid.UUID | None = None
    current_org_name: str | None = None
    etag: str | None = None
    tags: list[str] = Field(default_factory=list)


class OrgSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    org_id: uuid.UUID
    display_name: str
    legal_name: str | None = None
    domain: str | None = None
    etag: str | None = None


class CacheRecord(BaseModel):
    value: dict[str, Any]
    expires_at: float


class WebhookEvent(BaseModel):
    event_id: uuid.UUID
    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    tenant_id: uuid.UUID
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

