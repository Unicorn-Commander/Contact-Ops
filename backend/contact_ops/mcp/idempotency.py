"""MCP idempotency-key cache.

The production target is Redis with a 24h TTL. Phase 1 keeps the same contract
behind an in-process cache so handlers and tests exercise the exact call flow;
the module boundary is intentionally small for a Redis swap.
"""

from __future__ import annotations

import time
from typing import Any

from contact_ops.mcp.errors import IDEMPOTENCY_KEY_REUSED, ToolError
from contact_ops.mcp.registry import MCPContext

_TTL_SECONDS = 24 * 60 * 60
_CACHE: dict[tuple[str, str, str], tuple[bytes, dict[str, Any] | None, float]] = {}


def _tenant(ctx: MCPContext) -> str:
    return str(ctx.tenant_id)


def _prune(now: float) -> None:
    expired = [key for key, value in _CACHE.items() if value[2] <= now]
    for key in expired:
        _CACHE.pop(key, None)


async def check_or_register(
    ctx: MCPContext,
    *,
    idempotency_key: str | None,
    tool_name: str,
    stable_args_hash: bytes,
) -> dict[str, Any] | None:
    if idempotency_key is None:
        return None
    now = time.time()
    _prune(now)
    key = (_tenant(ctx), tool_name, idempotency_key)
    existing = _CACHE.get(key)
    if existing is not None:
        existing_hash, result, _expires_at = existing
        if existing_hash != stable_args_hash:
            raise ToolError(
                IDEMPOTENCY_KEY_REUSED,
                "idempotency key was reused with different parameters",
                retryable=False,
            )
        return result
    _CACHE[key] = (stable_args_hash, None, now + _TTL_SECONDS)
    return None


async def store_result(ctx: MCPContext, idempotency_key: str, result: dict[str, Any]) -> None:
    key_prefix = (_tenant(ctx),)
    for key, (args_hash, _old, expires_at) in list(_CACHE.items()):
        if key[:1] == key_prefix and key[2] == idempotency_key:
            _CACHE[key] = (args_hash, result, expires_at)
            return
