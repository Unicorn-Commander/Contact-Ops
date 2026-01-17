"""
Usage tracking for Data Intel API.

Tracks API calls per user/period and enforces tier limits.
"""

from datetime import datetime, date, timedelta
from typing import Optional
from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import get_tier_limits

logger = structlog.get_logger(__name__)


@dataclass
class UsageInfo:
    """Current usage information for a user."""
    user_id: str
    action: str
    used: int
    limit: Optional[int]
    period_start: date
    period_end: date
    remaining: Optional[int]
    is_limited: bool


async def get_current_period(period_days: int = 30) -> tuple[date, date]:
    """Get current billing period start and end dates."""
    today = date.today()
    # Simple: period starts on 1st of month
    period_start = today.replace(day=1)

    # Calculate end date
    if period_days == 7:
        # Weekly: 7 days from start of week
        days_since_monday = today.weekday()
        period_start = today - timedelta(days=days_since_monday)
        period_end = period_start + timedelta(days=6)
    elif period_days == 30:
        # Monthly: end of current month
        next_month = period_start.replace(day=28) + timedelta(days=4)
        period_end = next_month - timedelta(days=next_month.day)
    else:
        period_end = period_start + timedelta(days=period_days - 1)

    return period_start, period_end


async def get_usage(
    db: AsyncSession,
    user_id: str,
    action: str,
    tier: str,
) -> UsageInfo:
    """
    Get current usage for a user/action.

    Args:
        db: Database session
        user_id: User ID
        action: Action being performed (e.g., 'verify:single')
        tier: User's subscription tier

    Returns:
        UsageInfo with current usage and limits
    """
    # Get tier limits
    limits = await get_tier_limits(db, tier)
    period_days = limits.get("period_days", 30)

    # Determine limit based on action
    if action.startswith("verify:"):
        limit = limits.get("verifications_per_period")
    else:
        limit = None  # No limit for non-verification actions

    # Get current period
    period_start, period_end = await get_current_period(period_days)

    # Query current usage
    result = await db.execute(
        text("""
            SELECT COALESCE(SUM(count), 0) as total
            FROM usage_records
            WHERE user_id = :user_id
              AND action LIKE :action_prefix
              AND period_start = :period_start
        """),
        {
            "user_id": user_id,
            "action_prefix": f"{action.split(':')[0]}:%",  # verify:* counts together
            "period_start": period_start,
        }
    )
    row = result.fetchone()
    used = row[0] if row else 0

    # Calculate remaining
    remaining = None
    is_limited = False
    if limit is not None:
        remaining = max(0, limit - used)
        is_limited = remaining == 0

    return UsageInfo(
        user_id=user_id,
        action=action,
        used=used,
        limit=limit,
        period_start=period_start,
        period_end=period_end,
        remaining=remaining,
        is_limited=is_limited,
    )


async def increment_usage(
    db: AsyncSession,
    user_id: str,
    action: str,
    count: int = 1,
    api_key_id: Optional[str] = None,
) -> None:
    """
    Increment usage counter for a user/action.

    Uses UPSERT to atomically increment or create record.
    """
    period_start, period_end = await get_current_period()

    await db.execute(
        text("""
            INSERT INTO usage_records (id, user_id, api_key_id, action, count, period_start, period_end)
            VALUES (uuid_generate_v4(), :user_id, :api_key_id, :action, :count, :period_start, :period_end)
            ON CONFLICT (user_id, action, period_start)
            DO UPDATE SET
                count = usage_records.count + :count,
                updated_at = NOW()
        """),
        {
            "user_id": user_id,
            "api_key_id": api_key_id,
            "action": action,
            "count": count,
            "period_start": period_start,
            "period_end": period_end,
        }
    )
    await db.commit()

    logger.debug("Usage incremented", user_id=user_id, action=action, count=count)


async def check_usage_limit(
    db: AsyncSession,
    user_id: str,
    action: str,
    tier: str,
    count: int = 1,
) -> UsageInfo:
    """
    Check if user is within usage limits.

    Raises HTTPException if limit exceeded.
    """
    from fastapi import HTTPException, status

    usage = await get_usage(db, user_id, action, tier)

    if usage.is_limited:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": f"Rate limit exceeded for {action}",
                "code": "RATE_LIMITED",
                "limit": usage.limit,
                "used": usage.used,
                "resets_at": usage.period_end.isoformat(),
                "upgrade_url": "https://unicorncommander.ai/plans"
            },
            headers={
                "X-RateLimit-Limit": str(usage.limit or 0),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": usage.period_end.isoformat(),
            }
        )

    # Check if this request would exceed limit
    if usage.limit and (usage.used + count) > usage.limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": f"This request would exceed your {action} limit",
                "code": "WOULD_EXCEED_LIMIT",
                "limit": usage.limit,
                "used": usage.used,
                "requested": count,
                "remaining": usage.remaining,
                "resets_at": usage.period_end.isoformat(),
                "upgrade_url": "https://unicorncommander.ai/plans"
            }
        )

    return usage


def add_rate_limit_headers(response, usage: UsageInfo):
    """Add rate limit headers to response."""
    if usage.limit:
        response.headers["X-RateLimit-Limit"] = str(usage.limit)
        response.headers["X-RateLimit-Remaining"] = str(usage.remaining or 0)
        response.headers["X-RateLimit-Reset"] = usage.period_end.isoformat()
    response.headers["X-Subscription-Tier"] = usage.action  # Will be overridden
    return response
