"""Project-Ops case-file resolution with Redis caching.

The inbox detail pane shows a "Related work" card listing Project-Ops
projects/tasks tied to the proposal's aggregate (the person/org). This
module fetches that list via the Project-Ops MCP federation peer and
caches results in Redis with a 5-minute TTL (per Aaron's C1).

If Project-Ops is not reachable (or PROJECT_OPS_BASE_URL is unset),
``link_case_files`` returns an empty list rather than failing — the
inbox UI degrades gracefully to "no case files," and tier selection
just doesn't elevate the proposal to T4 on the case-file rule.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

import httpx
import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

_REDIS_KEY_PREFIX = "contactops:case_files:"
_TTL_SECONDS = 300  # 5 minutes per C1
_HTTP_TIMEOUT = 2.0  # don't block the inbox response on a slow federation peer


def _cache_key(tenant_id: uuid.UUID, aggregate_id: uuid.UUID) -> str:
    return f"{_REDIS_KEY_PREFIX}{tenant_id}:{aggregate_id}"


async def link_case_files(
    *,
    redis: Redis,
    tenant_id: uuid.UUID,
    aggregate_id: uuid.UUID,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Return [{project_id, project_name, task_id?, task_name?}] for an aggregate.

    Cache: Redis 5-minute TTL keyed by (tenant_id, aggregate_id). Cache miss
    triggers an MCP federation call to Project-Ops. Failures are cached as
    empty arrays for 60s to avoid hammering a downed peer.
    """
    base_url = os.environ.get("PROJECT_OPS_BASE_URL", "").rstrip("/")
    if not base_url:
        return []

    cached = await redis.get(_cache_key(tenant_id, aggregate_id))
    if cached is not None:
        try:
            return list(json.loads(cached))
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning(
                "case_file_cache.invalid_json",
                tenant_id=str(tenant_id),
                aggregate_id=str(aggregate_id),
            )

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
    try:
        result = await _fetch_case_files(
            client=client,
            base_url=base_url,
            tenant_id=tenant_id,
            aggregate_id=aggregate_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "case_file_fetch.failed",
            tenant_id=str(tenant_id),
            aggregate_id=str(aggregate_id),
            err=str(exc),
        )
        result = []
        await redis.set(_cache_key(tenant_id, aggregate_id), json.dumps(result), ex=60)
        return result
    finally:
        if owns_client:
            await client.aclose()

    await redis.set(
        _cache_key(tenant_id, aggregate_id),
        json.dumps(result),
        ex=_TTL_SECONDS,
    )
    return result


async def _fetch_case_files(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    tenant_id: uuid.UUID,
    aggregate_id: uuid.UUID,
) -> list[dict[str, Any]]:
    response = await client.post(
        f"{base_url}/mcp/tools/find_case_files_for_contact",
        json={"tenant_id": str(tenant_id), "contact_id": str(aggregate_id)},
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("links", [])
    if not isinstance(items, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "project_id" not in item or "project_name" not in item:
            continue
        cleaned.append(
            {
                "project_id": str(item["project_id"]),
                "project_name": str(item["project_name"]),
                "task_id": str(item["task_id"]) if item.get("task_id") else None,
                "task_name": str(item["task_name"]) if item.get("task_name") else None,
            }
        )
    return cleaned


async def link_case_files_batch(
    *,
    redis: Redis,
    tenant_id: uuid.UUID,
    aggregate_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """Batch lookup for the inbox list query.

    Runs ``link_case_files`` in parallel for up to 50 aggregates. Beyond
    that, the inbox list query is already paginated to 50/page so this
    upper bound is reached only via the rare "all clusters expanded"
    page.
    """
    if not aggregate_ids:
        return {}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        tasks = [
            link_case_files(
                redis=redis,
                tenant_id=tenant_id,
                aggregate_id=aid,
                http_client=client,
            )
            for aid in aggregate_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for aid, result in zip(aggregate_ids, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(
                "case_file_batch.item_failed",
                aggregate_id=str(aid),
                err=str(result),
            )
            out[aid] = []
        else:
            out[aid] = result
    return out


def _silence_unused_logging() -> None:
    """Suppress overly chatty httpx logs at DEBUG."""
    logging.getLogger("httpx").setLevel(logging.WARNING)


_silence_unused_logging()


__all__ = ["link_case_files", "link_case_files_batch"]
