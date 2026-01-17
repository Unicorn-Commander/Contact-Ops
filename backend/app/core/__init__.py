"""
Core modules for Center Deep Data Intel API.

Contains configuration, security, Redis connection, and shared utilities.
"""

from app.core.config import Settings, get_settings
from app.core.redis import (
    JOB_TTL_SECONDS,
    RedisUnavailableError,
    close_redis,
    create_job,
    delete_job,
    extend_job_ttl,
    get_job,
    get_redis,
    get_redis_context,
    init_redis,
    job_exists,
    update_job,
)
from app.core.security import AppContext, get_current_app, require_tier

__all__ = [
    # Config
    "Settings",
    "get_settings",
    # Security
    "AppContext",
    "get_current_app",
    "require_tier",
    # Redis
    "JOB_TTL_SECONDS",
    "RedisUnavailableError",
    "close_redis",
    "create_job",
    "delete_job",
    "extend_job_ttl",
    "get_job",
    "get_redis",
    "get_redis_context",
    "init_redis",
    "job_exists",
    "update_job",
]
