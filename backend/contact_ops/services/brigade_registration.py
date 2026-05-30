from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from contact_ops.core.config import Settings, get_settings
from contact_ops.mcp.registry import list_tools

logger = structlog.get_logger(__name__)

_last_successful_registration_at: str | None = None
_last_response: dict[str, Any] | None = None
_last_error: str | None = None


def build_brigade_descriptor(settings: Settings | None = None) -> dict[str, Any]:
    from contact_ops.mcp.tools import register_all_tools

    settings = settings or get_settings()
    register_all_tools()
    return {
        "service_id": "contact-ops",
        "service_name": "Contact-Ops",
        "mcp_endpoint": "https://mcp.contacts.magicunicorn.dev/mcp",
        "auth": {
            "type": "bearer",
            "issuer": settings.KEYCLOAK_ISSUER or "https://auth.magicunicorn.dev/realms/uchub",
            "audience": settings.KEYCLOAK_AUDIENCE,
            "pat_format": "co_pat_*",
        },
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_model.model_json_schema(),
                "scopes": list(tool.required_scopes),
            }
            for tool in list_tools()
        ],
        "agents": [
            {"slug": "ai-contacts-analyst", "kind": "chat_orchestrator"},
            {"slug": "dedup-agent", "kind": "background_processor"},
            {"slug": "confidence-approver", "kind": "background_processor"},
            {"slug": "quality-filter-agent", "kind": "background_processor"},
        ],
        "updated_at": datetime.now(UTC).isoformat(),
    }


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
