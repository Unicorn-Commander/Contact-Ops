"""Phase 1 Contact-Ops MCP tool registrations."""

from __future__ import annotations

import importlib

from contact_ops.mcp.registry import get_tool
from contact_ops.mcp.tools.people import register_people_tools
from contact_ops.mcp.tools.relationships import register_relationship_tools

_OPTIONAL_TOOL_MODULES = (
    "addresses",
    "carddav_passwords",
    "emails",
    "employment",
    "identifiers",
    "orgs",
    "phones",
    "tags",
)


def _import_optional_tool_modules() -> None:
    for module_name in _OPTIONAL_TOOL_MODULES:
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


__all__ = ["register_all_tools"]
