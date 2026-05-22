"""
Core modules for Contact-Ops API.

Contains configuration, security, Redis connection, and shared utilities.
"""

from contact_ops.core.config import Settings, get_settings
from contact_ops.core.redis import (
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
__all__ = [
    # Config
    "Settings",
    "get_settings",
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
