"""LLM-facing wrappers around curated Contact-Ops MCP tools."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, Field

from contact_ops.core.config import get_settings
from contact_ops.core.database import (
    async_session_maker,
    audit_session_maker,
    bind_session_context,
)
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
    "list_personal_access_tokens",
    "run_dedup_agent_now",
    "run_quality_filter_now",
    "run_confidence_approver_now",
    "get_brigade_registration_status",
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
        # uc_uid source: ctx.claims["uc_uid"] (normalized at JWT validation),
        # falling back to ctx.user_id. Use the original ctx.claims, not the
        # note-author-augmented local `claims`.
        uc_uid = str(ctx.claims.get("uc_uid") or ctx.user_id)
        async with async_session_maker() as db, audit_session_maker() as audit_db:
            await bind_session_context(db, str(ctx.tenant_id), uc_uid, settings)
            await bind_session_context(audit_db, str(ctx.tenant_id), uc_uid, settings)
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
        result = output.model_dump(mode="json")
        if name == "list_pending_proposals" and isinstance(result, dict):
            result = _compact_pending_proposals(result)
        return result


def _compact_pending_proposals(result: dict[str, Any]) -> dict[str, Any]:
    """Slim ``list_pending_proposals`` for the chat context.

    The full result carries every proposal's before/after payload (~1.8 KB each,
    ~30 k tokens for 50 rows) — fine for a 200 k cloud model but it overflows a
    locally-served model. This keeps just the fields needed to summarize/triage
    the queue, so the sovereign Qwen agent can reason over the WHOLE queue (all
    rows) inside its context window. Detail is one follow-up question away.
    """
    # Locally-served models can have an 8 k context (the midboy1 Qwen does), so
    # this stays MINIMAL — just enough to triage: the name alone distinguishes a
    # real person ("Glen Porter") from import junk ("Voip4", "WaterEmerg"). Drop
    # rationale (boilerplate, repeated 50×) and all heavy payloads.
    proposals = result.get("proposals") or []
    compact: list[dict[str, Any]] = []
    for p in proposals:
        if not isinstance(p, dict):
            continue
        compact.append(
            {
                "id": str(p.get("proposal_id", ""))[-6:],
                "name": p.get("entity_display_name"),
                "kind": p.get("entity_kind"),
                "action": p.get("action_type"),
                "confidence": p.get("confidence"),
                "fields": p.get("fields_changed"),
                "dedup": p.get("is_dedup"),
            }
        )
    return {
        "total_estimate": result.get("total_estimate"),
        "returned": len(compact),
        "note": "compact triage view — payloads + rationale omitted; ask about a specific id for detail",
        "proposals": compact,
    }


# Keys that are purely human-facing — dropping them from the JSON schema shrinks
# the tool-definition footprint by ~half (load-bearing for small-context local
# models like the 8 k Qwen, and cheaper for every model). Structural keys
# (type/properties/required/enum/items/anyOf/$ref/$defs/...) are preserved.
_SCHEMA_NOISE = frozenset({"description", "title", "default", "examples"})


def _lean_schema(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _lean_schema(v) for k, v in node.items() if k not in _SCHEMA_NOISE}
    if isinstance(node, list):
        return [_lean_schema(item) for item in node]
    return node


def _to_openai_tool(tool: ToolDef) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": (tool.description or "")[:140],
            "parameters": _lean_schema(tool.input_model.model_json_schema()),
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
