"""RBAC and OAuth scope checks for MCP tools."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, INSUFFICIENT_SCOPE, ToolError
from contact_ops.mcp.registry import MCPContext

ROLE_LADDER = {"CLIENT": 0, "STAFF": 1, "MANAGER": 2, "ADMIN": 3}
KNOWN_SCOPES = frozenset({"admin:debug"})


def get_role(claims: dict[str, Any]) -> str:
    roles = claims.get("realm_access", {}).get("roles", [])
    if isinstance(roles, str):
        roles = [roles]
    upper_roles = {str(role).upper() for role in roles}
    for role in ("ADMIN", "MANAGER", "STAFF", "CLIENT"):
        if role in upper_roles:
            return role
    return "CLIENT"


def get_scopes(claims: dict[str, Any]) -> set[str]:
    raw = claims.get("scope", "")
    if isinstance(raw, str):
        return {scope for scope in raw.split() if scope}
    if isinstance(raw, list):
        return {str(scope) for scope in raw}
    return set()


def require_role(ctx: MCPContext, minimum: str) -> None:
    current = get_role(ctx.claims)
    if ROLE_LADDER.get(current, -1) < ROLE_LADDER[minimum]:
        raise ToolError(INSUFFICIENT_ROLE, f"requires {minimum}", retryable=False)


def require_scopes(ctx: MCPContext, scopes: Iterable[str]) -> None:
    required = set(scopes)
    if not required:
        return
    granted = get_scopes(ctx.claims)
    missing = sorted(required - granted)
    if missing:
        raise ToolError(
            INSUFFICIENT_SCOPE,
            f"missing required scope(s): {', '.join(missing)}",
            retryable=False,
        )
