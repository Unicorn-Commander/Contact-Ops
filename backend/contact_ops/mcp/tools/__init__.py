"""Phase 1 Contact-Ops MCP tool registrations."""

from __future__ import annotations

import importlib

from contact_ops.mcp.registry import get_tool
from contact_ops.mcp.tools.people import register_people_tools
from contact_ops.mcp.tools.relationships import register_relationship_tools

_OPTIONAL_TOOL_MODULES = (
    "addresses",
    "agent_pipeline",
    "carddav_passwords",
    "connectors",
    "emails",
    "employment",
    "identifiers",
    "imports",
    "import_vcard",
    "import_csv",
    "notes",
    "orgs",
    "phones",
    "personal_access_tokens",
    "tags",
)


def _import_optional_tool_modules() -> None:
    for module_name in _OPTIONAL_TOOL_MODULES:
        try:
            importlib.import_module(f"contact_ops.mcp.tools.{module_name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"contact_ops.mcp.tools.{module_name}":
                raise


_PHASE_2_MODULES = ("tenancy", "acl", "photos", "voice")


def _import_phase_2_modules() -> None:
    for module_name in _PHASE_2_MODULES:
        try:
            importlib.import_module(f"contact_ops.mcp.tools.{module_name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"contact_ops.mcp.tools.{module_name}":
                raise


_PHASE_3_MODULES = (
    "agent_admin",
    "dedup_admin",
    "voice_match_admin",
    "inbox",
    "calibration_admin",
    "consumer_webhooks",
    "data_quality",
    "enrichment",
    "compliance",
)


def _import_phase_3_modules() -> None:
    for module_name in _PHASE_3_MODULES:
        try:
            importlib.import_module(f"contact_ops.mcp.tools.{module_name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"contact_ops.mcp.tools.{module_name}":
                raise


def register_all_tools() -> None:
    """Register Codex 1 tools and any optional peer-track tools present."""

    if get_tool("create_person") is None:
        register_people_tools()
    if get_tool("link_relationship") is None:
        register_relationship_tools()
    _import_optional_tool_modules()
    _import_phase_2_modules()
    _import_phase_3_modules()
    if get_tool("extract_ego_graph") is None:
        from contact_ops.mcp.tools.graph_admin import register_graph_tools

        register_graph_tools()


__all__ = ["register_all_tools"]
