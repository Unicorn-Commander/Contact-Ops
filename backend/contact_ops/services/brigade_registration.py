from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from contact_ops.core.config import Settings, get_settings
from contact_ops.mcp.entitlement import tier_for_tool
from contact_ops.mcp.registry import list_tools

logger = structlog.get_logger(__name__)

_last_successful_registration_at: str | None = None
_last_response: dict[str, Any] | None = None
_last_error: str | None = None


def build_brigade_descriptor(settings: Settings | None = None) -> dict[str, Any]:
    from contact_ops.mcp.tools import register_all_tools

    settings = settings or get_settings()
    register_all_tools()
    # Schema matches brigade.unicorncommander.ai/.well-known/mcps-schema-v1.json
    # per the federation integration doc Aaron pasted 2026-05-30. Key federation
    # primitives that go beyond the basic discovery shape:
    #   - tool_namespace : Brigade prepends "contact." to each tool name in
    #     unified catalogs (list_people → contact.list_people).
    #   - federated_auth.expected_audience : Brigade signs JWTs with this aud
    #     claim; our verifier MUST reject anything else.
    #   - per-tool tier_required + read_only : let Brigade pre-filter by
    #     customer tier and decide propose/confirm UX.
    return {
        "$schema": "https://brigade.unicorncommander.ai/.well-known/mcps-schema-v1.json",
        "name": "Contact-Ops",
        "version": settings.APP_VERSION,
        "service_id": "contact-ops",
        "service_name": "Contact-Ops",
        "mcp_endpoint": "https://mcp.contacts.magicunicorn.dev/mcp",
        "tool_namespace": "contact",
        "auth": {
            "type": "pat",
            "prefix": "co_pat_",
            "mint_url": "https://contacts.magicunicorn.dev/settings",
            "issuer": settings.KEYCLOAK_ISSUER or "https://auth.magicunicorn.dev/realms/uchub",
            "audience": settings.KEYCLOAK_AUDIENCE,
            "federated_auth": {
                "type": "brigade_jwt",
                "trusted_issuer": settings.BRIGADE_TRUSTED_ISSUER,
                "jwks_url": settings.BRIGADE_JWKS_URL,
                "expected_audience": settings.BRIGADE_EXPECTED_AUDIENCE,
                "signing_algorithm": "RS256",
            },
        },
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_model.model_json_schema(),
                "scopes_required": list(tool.required_scopes),
                # Real entitlement tier from the central policy (single source of
                # truth shared with the dispatch gate), NOT the old read-only
                # heuristic — so Brigade pre-filters by the tier we actually
                # enforce.
                "tier_required": tier_for_tool(tool.name),
                "read_only": _is_read_only(tool.name),
                "expected_latency_ms": 250 if _is_read_only(tool.name) else 800,
            }
            for tool in list_tools()
        ],
        "agents": [
            {"slug": "ai-contacts-analyst", "kind": "chat_orchestrator"},
            {"slug": "dedup-agent", "kind": "background_processor"},
            {"slug": "confidence-approver", "kind": "background_processor"},
            {"slug": "quality-filter-agent", "kind": "background_processor"},
        ],
        "resources": [
            {"uri_template": "people://list", "description": "Overview of all people in the tenant"},
            {"uri_template": "people://{person_id}", "description": "Single person record"},
            {"uri_template": "orgs://list", "description": "Overview of all organizations"},
            {"uri_template": "orgs://{org_id}", "description": "Single organization record"},
            {"uri_template": "proposals://pending", "description": "Pending Review Queue proposals"},
        ],
        "prompts": [
            {"name": "contact_summary", "description": "Summarize a person's interaction history + relationships"},
            {"name": "dedup_review", "description": "Audit pending dedup-agent merges before approval"},
        ],
        "rate_limits": {
            "per_user_per_minute": 60,
            "per_user_per_hour": 1000,
        },
        "contact": "REMOVED",
        "updated_at": datetime.now(UTC).isoformat(),
    }


# Read-only tool predicate — anything starting with these prefixes is flagged
# read-only in the manifest. Brigade can use this to pre-filter tools that
# don't need propose/confirm.
_READ_ONLY_PREFIXES = (
    "list_", "get_", "search_", "find_", "describe_", "show_", "preview_",
)


def _is_read_only(tool_name: str) -> bool:
    return tool_name.startswith(_READ_ONLY_PREFIXES)


async def register_with_brigade(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.BRIGADE_SERVICE_TOKEN:
        logger.warning("brigade_registration_skipped", reason="BRIGADE_SERVICE_TOKEN unset")
        return
    global _last_error, _last_response, _last_successful_registration_at
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                settings.BRIGADE_REGISTRY_URL,
                json=build_brigade_descriptor(settings),
                headers={"Authorization": f"Bearer {settings.BRIGADE_SERVICE_TOKEN}"},
            )
            response.raise_for_status()
            try:
                _last_response = response.json()
            except ValueError:
                _last_response = {"status_code": response.status_code, "text": response.text[:500]}
            _last_successful_registration_at = datetime.now(UTC).isoformat()
            _last_error = None
    except httpx.HTTPError as exc:
        _last_error = str(exc)
        logger.warning("brigade_registration_failed", error=str(exc))


def registration_status() -> dict[str, Any]:
    return {
        "last_successful_registration_at": _last_successful_registration_at,
        "last_response": _last_response,
        "last_error": _last_error,
    }
