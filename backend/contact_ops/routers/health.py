"""
Health check endpoints for Contact-Ops API.

Provides endpoints for Kubernetes probes and monitoring:
- /health - Basic health check
- /health/ready - Readiness probe (checks all downstream dependencies)
- /health/live - Liveness probe
"""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Response, status

from contact_ops.core.config import get_settings
from contact_ops.core.database import engine
from contact_ops.ops.schema_guard import get_schema_version

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])

_KEYCLOAK_CACHE: dict[str, Any] = {"data": None, "expires": 0.0}


@router.get(
    "",
    summary="Basic health check",
    response_model=dict[str, Any],
)
async def health_check() -> dict[str, Any]:
    """Basic health check — no dependency probes."""
    settings = get_settings()
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get(
    "/ready",
    summary="Readiness probe",
    response_model=dict[str, Any],
)
async def readiness_check(response: Response) -> dict[str, Any]:
    """Readiness probe — checks all downstream dependencies.

    Returns 200 if ready, 503 if any check fails.
    """
    settings = get_settings()
    checks: dict[str, dict[str, Any]] = {}
    all_healthy = True

    # Database — use shared engine, not a fresh one per call.
    checks["database"] = await _check_postgres(engine)
    checks["schema"] = await _check_schema(engine)

    # Redis
    checks["redis"] = await _check_redis(settings.REDIS_URL)

    # Qdrant
    checks["qdrant"] = await _check_qdrant(settings.QDRANT_URL)

    # FalkorDB
    checks["falkordb"] = await _check_falkordb(settings.FALKORDB_URL)

    # Garage
    checks["garage"] = await _check_garage(
        settings.GARAGE_ENDPOINT, settings.GARAGE_ADMIN_ENDPOINT
    )

    # Keycloak
    if settings.KEYCLOAK_ISSUER:
        checks["keycloak"] = await _check_keycloak(settings.KEYCLOAK_ISSUER)

    for v in checks.values():
        if v.get("status") != "healthy":
            all_healthy = False

    if not all_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if all_healthy else "not_ready",
        "service": settings.APP_NAME,
        "checks": checks,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get(
    "/live",
    summary="Liveness probe",
    response_model=dict[str, Any],
)
async def liveness_check() -> dict[str, Any]:
    return {
        "status": "alive",
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Per-service check helpers
# ---------------------------------------------------------------------------


async def _check_postgres(db_engine) -> dict[str, Any]:
    start = _time.monotonic()
    try:
        from sqlalchemy import text

        async with db_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        return {"status": "healthy", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        logger.error("database_health_check_failed", error=str(exc))
        return {"status": "unhealthy", "error": str(exc), "latency_ms": latency_ms}


async def _check_schema(db_engine) -> dict[str, Any]:
    start = _time.monotonic()
    try:
        version = await get_schema_version(db_engine)
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        if version.matches:
            return {
                "status": "healthy",
                "expected": version.expected,
                "actual": version.actual,
                "latency_ms": latency_ms,
            }
        return {
            "status": "unhealthy",
            "expected": version.expected,
            "actual": version.actual,
            "error": "alembic revision mismatch",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        logger.error("schema_health_check_failed", error=str(exc))
        return {"status": "unhealthy", "error": str(exc), "latency_ms": latency_ms}


async def _check_redis(redis_url: str) -> dict[str, Any]:
    start = _time.monotonic()
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_url, socket_connect_timeout=3)
        await client.ping()
        await client.close()
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        return {"status": "healthy", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        logger.error("redis_health_check_failed", error=str(exc))
        return {"status": "unhealthy", "error": str(exc), "latency_ms": latency_ms}


async def _check_qdrant(qdrant_url: str) -> dict[str, Any]:
    start = _time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{qdrant_url.rstrip('/')}/")
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        if resp.status_code == 200:
            return {"status": "healthy", "latency_ms": latency_ms}
        return {
            "status": "unhealthy",
            "error": f"status={resp.status_code}",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        logger.error("qdrant_health_check_failed", error=str(exc))
        return {"status": "unhealthy", "error": str(exc), "latency_ms": latency_ms}


async def _check_falkordb(falkordb_url: str) -> dict[str, Any]:
    """FalkorDB is Redis with the GRAPH module — PING is universal.

    Does NOT assume any specific db number.
    """
    start = _time.monotonic()
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(falkordb_url, socket_connect_timeout=3)
        await client.ping()
        await client.close()
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        return {"status": "healthy", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        logger.error("falkordb_health_check_failed", error=str(exc))
        return {"status": "unhealthy", "error": str(exc), "latency_ms": latency_ms}


async def _check_garage(
    s3_endpoint: str, admin_endpoint: str
) -> dict[str, Any]:
    """Check Garage health via admin port /health first, fall back to S3 HEAD."""
    start = _time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{admin_endpoint.rstrip('/')}/health")
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        if resp.status_code == 200:
            return {"status": "healthy", "latency_ms": latency_ms}
    except Exception as exc:
        logger.debug("garage_admin_health_failed_trying_s3", error=str(exc))

    # Fallback: HEAD the S3 endpoint
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.head(f"{s3_endpoint.rstrip('/')}/")
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        if resp.status_code < 500:
            return {"status": "healthy", "latency_ms": latency_ms}
        return {
            "status": "unhealthy",
            "error": f"status={resp.status_code}",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        logger.error("garage_health_check_failed", error=str(exc))
        return {"status": "unhealthy", "error": str(exc), "latency_ms": latency_ms}


async def _check_keycloak(issuer_url: str) -> dict[str, Any]:
    """Fetch .well-known/openid-configuration. Cache for 60s."""
    global _KEYCLOAK_CACHE
    now = _time.time()
    if _KEYCLOAK_CACHE["data"] is not None and now < _KEYCLOAK_CACHE["expires"] - 5:
        return _KEYCLOAK_CACHE["data"]  # type: ignore[return-value]

    start = _time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"
            )
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        if resp.status_code == 200:
            result = {"status": "healthy", "latency_ms": latency_ms}
        else:
            result = {
                "status": "unhealthy",
                "error": f"status={resp.status_code}",
                "latency_ms": latency_ms,
            }
    except Exception as exc:
        latency_ms = round((_time.monotonic() - start) * 1000, 1)
        logger.error("keycloak_health_check_failed", error=str(exc))
        result = {"status": "unhealthy", "error": str(exc), "latency_ms": latency_ms}

    _KEYCLOAK_CACHE = {"data": result, "expires": now + 60}
    return result
