"""
Usage tracking API endpoints for Data Intel.

Allows users to check their usage and limits.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import AppContext, get_current_app
from app.core.usage_tracker import get_usage, get_current_period
from app.core.rbac import get_tier_limits

router = APIRouter(prefix="/api/v1/usage", tags=["Usage"])


@router.get("/current")
async def get_current_usage(
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
):
    """Get current usage statistics for authenticated user."""
    if not app.user_id:
        return {"error": "No user context"}

    # Get limits for tier
    limits = await get_tier_limits(db, app.tier)
    period_start, period_end = await get_current_period(limits.get("period_days", 30))

    # Get verification usage
    verify_usage = await get_usage(db, app.user_id, "verify:single", app.tier)

    return {
        "user_id": app.user_id,
        "tier": app.tier,
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "verifications": {
            "used": verify_usage.used,
            "limit": verify_usage.limit,
            "remaining": verify_usage.remaining,
        },
        "permissions": app.permissions,
    }


@router.get("/limits")
async def get_tier_info(
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
):
    """Get tier limits and permissions."""
    limits = await get_tier_limits(db, app.tier)

    return {
        "tier": app.tier,
        "limits": limits,
        "permissions": app.permissions,
    }
