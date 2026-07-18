"""MCP write-tool idempotency store: Redis-backed with in-process fallback."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contact_ops.mcp import idempotency
from contact_ops.mcp.errors import IDEMPOTENCY_KEY_REUSED, ToolError


def _ctx() -> SimpleNamespace:
    # check_or_register/store_result only read ctx.tenant_id.
    return SimpleNamespace(tenant_id=uuid.uuid4())


@pytest.fixture
def _fake_redis(monkeypatch: pytest.MonkeyPatch):
    import fakeredis.aioredis  # type: ignore[import-untyped]

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _get_redis():
        return client

    monkeypatch.setattr("contact_ops.core.redis.get_redis", _get_redis)
    return client


@pytest.mark.asyncio
async def test_redis_claim_store_and_replay(_fake_redis) -> None:
    ctx = _ctx()
    key = str(uuid.uuid4())
    args = b"stable-args-hash"

    # First sight: claims the key, no cached result yet.
    first = await idempotency.check_or_register(
        ctx, idempotency_key=key, tool_name="create_person", stable_args_hash=args
    )
    assert first is None

    await idempotency.store_result(ctx, key, {"person_id": "p1"})

    # Replay with same args returns the stored result.
    replay = await idempotency.check_or_register(
        ctx, idempotency_key=key, tool_name="create_person", stable_args_hash=args
    )
    assert replay == {"person_id": "p1"}


@pytest.mark.asyncio
async def test_redis_reuse_with_different_args_raises(_fake_redis) -> None:
    ctx = _ctx()
    key = str(uuid.uuid4())
    await idempotency.check_or_register(
        ctx, idempotency_key=key, tool_name="create_person", stable_args_hash=b"aaa"
    )
    with pytest.raises(ToolError) as exc:
        await idempotency.check_or_register(
            ctx, idempotency_key=key, tool_name="create_person", stable_args_hash=b"bbb"
        )
    assert exc.value.code == IDEMPOTENCY_KEY_REUSED


@pytest.mark.asyncio
async def test_redis_is_shared_across_contexts_same_tenant(_fake_redis) -> None:
    """Two independent MCPContexts on the same tenant share the Redis claim.

    This is the whole point of the move off the in-process dict: a key claimed on
    one worker replica is visible to another. Same-tenant, distinct context
    objects stand in for two replicas.
    """
    tenant = uuid.uuid4()
    ctx_a = SimpleNamespace(tenant_id=tenant)
    ctx_b = SimpleNamespace(tenant_id=tenant)
    key = str(uuid.uuid4())
    args = b"h"

    assert (
        await idempotency.check_or_register(
            ctx_a, idempotency_key=key, tool_name="create_person", stable_args_hash=args
        )
        is None
    )
    await idempotency.store_result(ctx_a, key, {"person_id": "shared"})

    # ctx_b (a different "replica") sees the result registered by ctx_a.
    seen = await idempotency.check_or_register(
        ctx_b, idempotency_key=key, tool_name="create_person", stable_args_hash=args
    )
    assert seen == {"person_id": "shared"}


@pytest.mark.asyncio
async def test_falls_back_to_in_process_when_redis_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contact_ops.core.redis import RedisUnavailableError

    async def _boom():
        raise RedisUnavailableError("not initialised")

    monkeypatch.setattr("contact_ops.core.redis.get_redis", _boom)
    idempotency._CACHE.clear()

    ctx = _ctx()
    key = str(uuid.uuid4())
    args = b"h"

    first = await idempotency.check_or_register(
        ctx, idempotency_key=key, tool_name="create_person", stable_args_hash=args
    )
    assert first is None
    await idempotency.store_result(ctx, key, {"person_id": "local"})
    replay = await idempotency.check_or_register(
        ctx, idempotency_key=key, tool_name="create_person", stable_args_hash=args
    )
    assert replay == {"person_id": "local"}


@pytest.mark.asyncio
async def test_none_key_is_a_noop(_fake_redis) -> None:
    ctx = _ctx()
    result = await idempotency.check_or_register(
        ctx, idempotency_key=None, tool_name="create_person", stable_args_hash=b"h"
    )
    assert result is None
