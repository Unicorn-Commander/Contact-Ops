"""LLM-facing wrappers around curated Contact-Ops MCP tools."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import text

from contact_ops.core.config import get_settings
from contact_ops.core.database import async_session_maker, audit_session_maker
from contact_ops.mcp.errors import ToolError
from contact_ops.mcp.registry import MCPContext, ToolDef, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.common import enforce_tool_rbac

register_all_tools()

CURATED_MCP_TOOLS: tuple[str, ...] = (
    "find_person_by_identifier",
    "list_people",
    "list_organizations",
    "get_person",
    "get_organization",
    "list_pending_proposals",
    "suggest_relationships",
    "propose_merge",
    "list_notes",
    "append_note",
    "propose_vcard_records",
    "list_connectors",
    "pull_connector_now",
    "list_connector_runs",
)

AI_CONTACTS_ANALYST_AGENT_SLUG = "ai-contacts-analyst"


class ChatRequestContext(BaseModel):
    """Trusted request context resolved from JWT middleware."""

    tenant_id: uuid.UUID
    user_id: str
    actor_chain: dict[str, Any]
    human_authority: str
    claims: dict[str, Any]
    request_id: str


class AskBrigadeInput(BaseModel):
    query: str = Field(min_length=1, max_length=4000)


class ChatToolExecutor:
    """Expose safe MCP tools as OpenAI function tools and execute them."""

    def definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for name in CURATED_MCP_TOOLS:
            tool = get_tool(name)
            if tool is None:
                continue
            definitions.append(_to_openai_tool(tool))
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": "ask_brigade",
                    "description": (
                        "Route a question to Brigade for cross-system research. "
                        "TODO: phase 2 will wire real Brigade chat integration."
                    ),
                    "parameters": AskBrigadeInput.model_json_schema(),
                },
            }
        )
        return definitions

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        ctx: ChatRequestContext,
    ) -> dict[str, Any]:
        if name == "ask_brigade":
            return await _ask_brigade(args)
        if name not in CURATED_MCP_TOOLS or name.startswith("apply_"):
            return {"error": f"tool '{name}' is not available to chat"}

        tool = get_tool(name)
        if tool is None:
            return {"error": f"tool '{name}' is not registered"}

        try:
            parsed = tool.input_model.model_validate(args)
        except Exception as exc:
            return {"error": f"invalid arguments: {exc}"}

        claims = ctx.claims
        if name == "append_note":
            claims = {
                **ctx.claims,
                "_contact_ops_note_author": {
                    "author_type": "agent",
                    "author_id": AI_CONTACTS_ANALYST_AGENT_SLUG,
                },
            }

        settings = get_settings()
        async with async_session_maker() as db, audit_session_maker() as audit_db:
            await db.execute(
                text("SELECT set_config(:k, :v, true)"),
                {"k": settings.TENANT_GUC_NAME, "v": str(ctx.tenant_id)},
            )
            await audit_db.execute(
                text("SELECT set_config(:k, :v, true)"),
                {"k": settings.TENANT_GUC_NAME, "v": str(ctx.tenant_id)},
            )
            mcp_ctx = MCPContext(
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                actor_chain=ctx.actor_chain,
                human_authority=ctx.human_authority,
                db=db,
                audit_db=audit_db,
                request_id=ctx.request_id,
                claims=claims,
            )
            try:
                enforce_tool_rbac(mcp_ctx, tool)
                output = await tool.handler(mcp_ctx, parsed)
                await db.commit()
                await audit_db.commit()
            except ToolError as exc:
                await db.rollback()
                await audit_db.rollback()
                return {"error": exc.message, "code": exc.code, "retryable": exc.retryable}
            except Exception:
                await db.rollback()
                await audit_db.rollback()
                raise
        return output.model_dump(mode="json")


def _to_openai_tool(tool: ToolDef) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_model.model_json_schema(),
        },
    }


async def _ask_brigade(args: dict[str, Any]) -> dict[str, Any]:
    payload = AskBrigadeInput.model_validate(args)
    # TODO(chat phase 2): send authenticated Contact-Ops context and stream or
    # reconcile the Brigade answer. This placeholder keeps the tool contract
    # visible to the model without making Brigade authoritative yet.
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                "https://brigade.magicunicorn.dev/v1/chat",
                json={"query": payload.query, "source": "contact-ops-chat-placeholder"},
            )
    except httpx.HTTPError:
        pass
    return {"todo": "brigade integration phase 2"}


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must be a JSON object")
    return parsed
