"""
Redis connection and job storage for Contact-Ops.

Provides async Redis client for storing batch job data with proper TTL management.
Uses redis-py with async support (redis.asyncio).
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from contact_ops.core.config import get_settings

logger = structlog.get_logger(__name__)

# Global Redis client instance
_redis_client: Redis | None = None

# Default TTL for job data (24 hours)
JOB_TTL_SECONDS = 86400  # 24 * 60 * 60


class RedisUnavailableError(Exception):
    """Raised when Redis connection is unavailable."""
    pass


def _json_serializer(obj: Any) -> str:
    """Custom JSON serializer that handles datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def init_redis() -> Redis:
    """
    Initialize the Redis connection.

    Should be called on application startup.

    Returns:
        Redis client instance

    Raises:
        RedisError: If connection cannot be established
    """
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    settings = get_settings()

    logger.info(
        "Initializing Redis connection",
        redis_url=settings.REDIS_URL.split("@")[-1] if "@" in settings.REDIS_URL else settings.REDIS_URL,
    )

    try:
        _redis_client = Redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )

        # Test connection
        await _redis_client.ping()

        logger.info("Redis connection established successfully")
        return _redis_client

    except RedisError as e:
        logger.error("Failed to connect to Redis", error=str(e))
        _redis_client = None
        raise


async def close_redis() -> None:
    """
    Close the Redis connection.

    Should be called on application shutdown.
    """
    global _redis_client

    if _redis_client is not None:
        logger.info("Closing Redis connection")
        await _redis_client.close()
        _redis_client = None


async def get_redis() -> Redis:
    """
    Get the Redis client instance.

    Returns:
        Redis client instance

    Raises:
        RedisUnavailableError: If Redis is not initialized
    """
    if _redis_client is None:
        raise RedisUnavailableError("Redis client is not initialized. Call init_redis() first.")
    return _redis_client


@asynccontextmanager
async def get_redis_context() -> AsyncGenerator[Redis, None]:
    """
    Context manager for Redis operations with error handling.

    Usage:
        async with get_redis_context() as redis:
            await redis.set("key", "value")

    Yields:
        Redis client instance

    Raises:
        RedisUnavailableError: If Redis is not available
    """
    try:
        redis = await get_redis()
        yield redis
    except RedisError as e:
        logger.error("Redis operation failed", error=str(e))
        raise RedisUnavailableError(f"Redis operation failed: {str(e)}") from e


# ============================================================================
# Job Storage Functions
# ============================================================================


def _job_key(job_id: str) -> str:
    """Generate Redis key for a job."""
    return f"job:{job_id}"


async def create_job(
    job_id: str,
    job_data: dict[str, Any],
    ttl_seconds: int = JOB_TTL_SECONDS,
) -> bool:
    """
    Create a new job record in Redis.

    Args:
        job_id: Unique job identifier
        job_data: Job data dictionary (must be JSON-serializable)
        ttl_seconds: Time-to-live in seconds (default: 24 hours)

    Returns:
        True if job was created successfully

    Raises:
        RedisUnavailableError: If Redis is not available
    """
    try:
        redis = await get_redis()
        key = _job_key(job_id)

        # Serialize job data to JSON
        json_data = json.dumps(job_data, default=_json_serializer)

        # Set with TTL
        await redis.setex(key, ttl_seconds, json_data)

        logger.debug("Job created in Redis", job_id=job_id, ttl_seconds=ttl_seconds)
        return True

    except RedisError as e:
        logger.error("Failed to create job in Redis", job_id=job_id, error=str(e))
        raise RedisUnavailableError(f"Failed to create job: {str(e)}") from e


async def get_job(job_id: str) -> dict[str, Any] | None:
    """
    Retrieve a job record from Redis.

    Args:
        job_id: Job identifier

    Returns:
        Job data dictionary or None if not found

    Raises:
        RedisUnavailableError: If Redis is not available
    """
    try:
        redis = await get_redis()
        key = _job_key(job_id)

        json_data = await redis.get(key)

        if json_data is None:
            return None

        return json.loads(json_data)

    except RedisError as e:
        logger.error("Failed to get job from Redis", job_id=job_id, error=str(e))
        raise RedisUnavailableError(f"Failed to get job: {str(e)}") from e


async def update_job(
    job_id: str,
    updates: dict[str, Any],
    ttl_seconds: int | None = None,
) -> bool:
    """
    Update a job record in Redis.

    Performs a read-modify-write operation to update specific fields.
    Preserves existing TTL unless a new one is specified.

    Args:
        job_id: Job identifier
        updates: Dictionary of fields to update
        ttl_seconds: Optional new TTL (if None, preserves existing TTL)

    Returns:
        True if job was updated successfully, False if job not found

    Raises:
        RedisUnavailableError: If Redis is not available
    """
    try:
        redis = await get_redis()
        key = _job_key(job_id)

        # Get existing data
        json_data = await redis.get(key)
        if json_data is None:
            logger.warning("Job not found for update", job_id=job_id)
            return False

        # Parse existing data
        job_data = json.loads(json_data)

        # Apply updates
        job_data.update(updates)

        # Serialize updated data
        updated_json = json.dumps(job_data, default=_json_serializer)

        # Get remaining TTL if not explicitly provided
        if ttl_seconds is None:
            remaining_ttl = await redis.ttl(key)
            if remaining_ttl > 0:
                ttl_seconds = remaining_ttl
            else:
                ttl_seconds = JOB_TTL_SECONDS

        # Save with TTL
        await redis.setex(key, ttl_seconds, updated_json)

        logger.debug(
            "Job updated in Redis",
            job_id=job_id,
            updated_fields=list(updates.keys()),
        )
        return True

    except RedisError as e:
        logger.error("Failed to update job in Redis", job_id=job_id, error=str(e))
        raise RedisUnavailableError(f"Failed to update job: {str(e)}") from e


async def delete_job(job_id: str) -> bool:
    """
    Delete a job record from Redis.

    Args:
        job_id: Job identifier

    Returns:
        True if job was deleted, False if job did not exist

    Raises:
        RedisUnavailableError: If Redis is not available
    """
    try:
        redis = await get_redis()
        key = _job_key(job_id)

        result = await redis.delete(key)

        if result > 0:
            logger.debug("Job deleted from Redis", job_id=job_id)
            return True
        return False

    except RedisError as e:
        logger.error("Failed to delete job from Redis", job_id=job_id, error=str(e))
        raise RedisUnavailableError(f"Failed to delete job: {str(e)}") from e


async def job_exists(job_id: str) -> bool:
    """
    Check if a job exists in Redis.

    Args:
        job_id: Job identifier

    Returns:
        True if job exists, False otherwise

    Raises:
        RedisUnavailableError: If Redis is not available
    """
    try:
        redis = await get_redis()
        key = _job_key(job_id)

        return await redis.exists(key) > 0

    except RedisError as e:
        logger.error("Failed to check job existence in Redis", job_id=job_id, error=str(e))
        raise RedisUnavailableError(f"Failed to check job existence: {str(e)}") from e


async def extend_job_ttl(job_id: str, ttl_seconds: int = JOB_TTL_SECONDS) -> bool:
    """
    Extend the TTL of a job record.

    Args:
        job_id: Job identifier
        ttl_seconds: New TTL in seconds

    Returns:
        True if TTL was extended, False if job not found

    Raises:
        RedisUnavailableError: If Redis is not available
    """
    try:
        redis = await get_redis()
        key = _job_key(job_id)

        result = await redis.expire(key, ttl_seconds)
        return bool(result)

    except RedisError as e:
        logger.error("Failed to extend job TTL in Redis", job_id=job_id, error=str(e))
        raise RedisUnavailableError(f"Failed to extend job TTL: {str(e)}") from e
