"""MCP idempotency-key cache.

Write-tool idempotency keys are shared state: the MCP surface runs behind
several worker replicas, so a key registered on one replica must be visible to
the others (and survive a restart) or the same ``idempotency_key`` can execute
a write twice. The production store is therefore Redis (24h TTL) — the same
shared Redis the rest of the ecosystem uses (``contact_ops.core.redis``).

When Redis is unavailable (unit tests that never call ``init_redis``, or a Redis
outage) the module degrades to a per-process dict — best-effort dedup within one
worker rather than a hard failure. This mirrors the fail-open posture of the
rate-limit middleware: idempotency is a safety convenience, never a request
blocker, so a Redis hiccup must not 500 a write.

Contract (unchanged across both backends):

* ``check_or_register`` claims the key on first sight (returns ``None`` so the
  caller proceeds), returns the stored result on a replay with the SAME args,
  and raises ``IDEMPOTENCY_KEY_REUSED`` on a replay with DIFFERENT args.
* ``store_result`` records the handler's output against a previously-claimed
  key so a later replay returns it verbatim.
"""

from __future__ import annotations

import json
import time
from typing import Any

import structlog

from contact_ops.mcp.errors import IDEMPOTENCY_KEY_REUSED, ToolError
from contact_ops.mcp.registry import MCPContext

logger = structlog.get_logger(__name__)

_TTL_SECONDS = 24 * 60 * 60

# Per-process fallback, only used when Redis is unreachable. Key is
# (tenant, tool_name, idempotency_key); value is (args_hash, result, expires_at).
_CACHE: dict[tuple[str, str, str], tuple[bytes, dict[str, Any] | None, float]] = {}


def _tenant(ctx: MCPContext) -> str:
    return str(ctx.tenant_id)


def _redis_key(ctx: MCPContext, idempotency_key: str) -> str:
    """Redis key for a claim.

    Keyed on (tenant, idempotency_key) — the tool name is intentionally NOT in
    the key so ``store_result`` (which has no tool_name) can reconstruct it. A
    key reused across two different tools still trips the reuse check because the
    stored args hash differs, which is the correct "reused with different
    parameters" outcome.
    """
    return f"mcp:idem:{_tenant(ctx)}:{idempotency_key}"


async def _maybe_redis() -> Any | None:
    """Return the shared Redis client, or ``None`` when it is unavailable.

    Never raises: a missing/broken Redis falls back to the in-process cache.
    """
    try:
        from contact_ops.core.redis import RedisUnavailableError, get_redis

        return await get_redis()
    except Exception as exc:  # noqa: BLE001 — degrade to in-process, never 500
        # RedisUnavailableError is the common (uninitialised) case; any other
        # error is treated the same — fall back rather than fail the write.
        if exc.__class__.__name__ != "RedisUnavailableError":
            logger.warning("mcp_idempotency_redis_unavailable", error=str(exc))
        return None


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

    redis = await _maybe_redis()
    if redis is None:
        return _check_or_register_local(
            ctx,
            idempotency_key=idempotency_key,
            tool_name=tool_name,
            stable_args_hash=stable_args_hash,
        )

    key = _redis_key(ctx, idempotency_key)
    hash_hex = stable_args_hash.hex()
    placeholder = json.dumps({"hash": hash_hex, "result": None})
    try:
        # Atomically claim the key: SET NX only succeeds for the first caller.
        claimed = await redis.set(key, placeholder, nx=True, ex=_TTL_SECONDS)
        if claimed:
            return None
        raw = await redis.get(key)
        if raw is None:
            # Expired between the NX miss and the read — re-claim and proceed.
            await redis.set(key, placeholder, ex=_TTL_SECONDS)
            return None
    except Exception as exc:  # noqa: BLE001 — degrade to in-process on Redis error
        logger.warning("mcp_idempotency_redis_error", error=str(exc))
        return _check_or_register_local(
            ctx,
            idempotency_key=idempotency_key,
            tool_name=tool_name,
            stable_args_hash=stable_args_hash,
        )

    data = raw if isinstance(raw, (dict,)) else json.loads(raw)
    if data.get("hash") != hash_hex:
        raise ToolError(
            IDEMPOTENCY_KEY_REUSED,
            "idempotency key was reused with different parameters",
            retryable=False,
        )
    return data.get("result")


async def store_result(
    ctx: MCPContext, idempotency_key: str, result: dict[str, Any]
) -> None:
    redis = await _maybe_redis()
    if redis is None:
        _store_result_local(ctx, idempotency_key, result)
        return

    key = _redis_key(ctx, idempotency_key)
    try:
        raw = await redis.get(key)
        if raw is None:
            # Claim vanished (TTL) — nothing to attach the result to.
            return
        data = raw if isinstance(raw, dict) else json.loads(raw)
        data["result"] = result
        # Preserve the remaining TTL so the completed result expires on the same
        # 24h clock as its claim rather than being extended on every replay.
        ttl = await redis.ttl(key)
        ttl_seconds = ttl if isinstance(ttl, int) and ttl > 0 else _TTL_SECONDS
        await redis.set(key, json.dumps(data), ex=ttl_seconds)
    except Exception as exc:  # noqa: BLE001 — best-effort; a lost result just re-runs
        logger.warning("mcp_idempotency_store_error", error=str(exc))
        _store_result_local(ctx, idempotency_key, result)


# ---------------------------------------------------------------------------
# In-process fallback (Redis unavailable). Best-effort, per-worker only.
# ---------------------------------------------------------------------------


def _check_or_register_local(
    ctx: MCPContext,
    *,
    idempotency_key: str,
    tool_name: str,
    stable_args_hash: bytes,
) -> dict[str, Any] | None:
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


def _store_result_local(
    ctx: MCPContext, idempotency_key: str, result: dict[str, Any]
) -> None:
    key_prefix = (_tenant(ctx),)
    for key, (args_hash, _old, expires_at) in list(_CACHE.items()):
        if key[:1] == key_prefix and key[2] == idempotency_key:
            _CACHE[key] = (args_hash, result, expires_at)
            return
