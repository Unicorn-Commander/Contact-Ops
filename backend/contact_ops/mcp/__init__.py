"""MCP server package for Contact-Ops."""
# ruff: noqa: I001

from contact_ops.mcp.errors import ToolError  # noqa: F401
from contact_ops.mcp.registry import MCPContext, ToolDef, get_tool, list_tools, register_tool  # noqa: F401
from contact_ops.mcp.server import router  # noqa: F401

__all__ = [
    "MCPContext",
    "ToolDef",
    "ToolError",
    "get_tool",
    "list_tools",
    "register_tool",
    "router",
]
