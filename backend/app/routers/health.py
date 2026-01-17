"""
Health check endpoints for Center Deep Data Intel API.

Provides endpoints for Kubernetes probes and monitoring:
- /health - Basic health check
- /health/ready - Readiness probe (checks dependencies)
- /health/live - Liveness probe
"""

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Response, status

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "",
    summary="Basic health check",
    description="Returns basic health status of the API service.",
    response_model=dict[str, Any],
)
async def health_check() -> dict[str, Any]:
    """
    Basic health check endpoint.

    Returns the service status and basic information.
    This endpoint does not check dependencies.
    """
    settings = get_settings()

    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get(
    "/ready",
    summary="Readiness probe",
    description="Checks if the service is ready to accept traffic. Validates database and Redis connections.",
    response_model=dict[str, Any],
)
async def readiness_check(response: Response) -> dict[str, Any]:
    """
    Readiness probe for Kubernetes.

    Checks that all required dependencies are available:
    - Database connection
    - Redis connection

    Returns 200 if ready, 503 if not ready.
    """
    settings = get_settings()
    checks: dict[str, dict[str, Any]] = {}
    all_healthy = True

    # Check database connection
    try:
        # Import here to avoid circular imports and allow graceful degradation
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(
            settings.DATABASE_URL,
            pool_pre_ping=True,
            pool_size=1,
        )
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()

        checks["database"] = {
            "status": "healthy",
            "latency_ms": None,  # Could add timing here
        }
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        checks["database"] = {
            "status": "unhealthy",
            "error": str(e),
        }
        all_healthy = False

    # Check Redis connection
    try:
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        await redis_client.ping()
        await redis_client.close()

        checks["redis"] = {
            "status": "healthy",
            "latency_ms": None,
        }
    except Exception as e:
        logger.error("Redis health check failed", error=str(e))
        checks["redis"] = {
            "status": "unhealthy",
            "error": str(e),
        }
        all_healthy = False

    # Set response status based on health
    if not all_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if all_healthy else "not_ready",
        "service": settings.APP_NAME,
        "checks": checks,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get(
    "/live",
    summary="Liveness probe",
    description="Simple liveness check. Returns 200 if the process is alive.",
    response_model=dict[str, Any],
)
async def liveness_check() -> dict[str, Any]:
    """
    Liveness probe for Kubernetes.

    Simple check that the process is alive and responding.
    This should always return 200 unless the process is deadlocked.
    """
    return {
        "status": "alive",
        "timestamp": datetime.utcnow().isoformat(),
    }
