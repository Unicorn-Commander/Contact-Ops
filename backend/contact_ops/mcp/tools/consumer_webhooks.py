"""Admin MCP tools for consumer webhook subscriptions."""

from __future__ import annotations

import secrets
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import text

from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register


class RegisterWebhookInput(BaseModel):
    consumer_app: Literal[
        "listing-ops",
        "crisis-ops",
        "project-ops",
        "meeting-ops",
        "stable",
        "brigade",
    ]
    url: HttpUrl
    event_kinds: list[str] = Field(min_length=1, max_length=50)
    hmac_secret: str | None = Field(default=None, min_length=32, max_length=256)


class RegisterWebhookOutput(ToolOutput):
    consumer_app: str
    event_kinds: list[str]
    secret_generated: bool


async def _handle_register_webhook(
    ctx: MCPContext,
    payload: RegisterWebhookInput,
) -> RegisterWebhookOutput:
    secret = payload.hmac_secret or secrets.token_urlsafe(48)
    await ctx.db.execute(
        text(
            """
            INSERT INTO consumer_webhook_subscription (
                consumer_app_id, tenant_id, url, event_kinds, hmac_secret, active
            )
            VALUES (:consumer_app_id, :tenant_id, :url, :event_kinds, :hmac_secret, true)
            ON CONFLICT (consumer_app_id, tenant_id) DO UPDATE
            SET url = EXCLUDED.url,
                event_kinds = EXCLUDED.event_kinds,
                hmac_secret = EXCLUDED.hmac_secret,
                active = true
            """
        ),
        {
            "consumer_app_id": payload.consumer_app,
            "tenant_id": ctx.tenant_id,
            "url": str(payload.url),
            "event_kinds": payload.event_kinds,
            "hmac_secret": secret,
        },
    )
    return RegisterWebhookOutput(
        consumer_app=payload.consumer_app,
        event_kinds=payload.event_kinds,
        secret_generated=payload.hmac_secret is None,
    )


def register_consumer_webhook_tools() -> None:
    register(
        name="register_webhook",
        description="Register or replace a best-effort Contact-Ops consumer webhook subscription.",
        input_model=RegisterWebhookInput,
        output_model=RegisterWebhookOutput,
        handler=_handle_register_webhook,
        required_role="ADMIN",
        required_scopes=("contactops:webhooks.admin",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        idempotency="idempotent",
    )


register_consumer_webhook_tools()

