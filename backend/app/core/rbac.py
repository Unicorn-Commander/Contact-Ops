"""
RBAC (Role-Based Access Control) for Data Intel.

Manages tier-based permissions and access control.
"""

from enum import Enum
from typing import Optional
import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class Permission(str, Enum):
    """Available permissions in the system."""
    # Verification
    VERIFY_SINGLE = "verify:single"
    VERIFY_BATCH = "verify:batch"

    # Catalogue - Write
    CATALOGUE_SUBMIT = "catalogue:submit"
    CATALOGUE_UPLOAD = "catalogue:upload"

    # Catalogue - Read (own data)
    CATALOGUE_SEARCH_OWN = "catalogue:search:own"
    CATALOGUE_LOOKUP_OWN = "catalogue:lookup:own"

    # Catalogue - Read (cross-app)
    CATALOGUE_SEARCH_CROSS = "catalogue:search:cross"
    CATALOGUE_LOOKUP_CROSS = "catalogue:lookup:cross"

    # Admin
    ADMIN_KEYS = "admin:keys"
    ADMIN_STATS = "admin:stats"


# Default tier permissions (fallback if DB unavailable)
DEFAULT_TIER_PERMISSIONS = {
    "trial": [Permission.VERIFY_SINGLE],
    "basic": [Permission.VERIFY_SINGLE, Permission.VERIFY_BATCH,
              Permission.CATALOGUE_SUBMIT, Permission.ADMIN_KEYS],
    "pro": [Permission.VERIFY_SINGLE, Permission.VERIFY_BATCH,
            Permission.CATALOGUE_SUBMIT, Permission.CATALOGUE_UPLOAD,
            Permission.CATALOGUE_SEARCH_OWN, Permission.CATALOGUE_LOOKUP_OWN,
            Permission.ADMIN_KEYS, Permission.ADMIN_STATS],
    "enterprise": [p for p in Permission],  # All permissions
    "unlimited": [p for p in Permission],   # Admin/standalone
}

# Tier limits (fallback)
DEFAULT_TIER_LIMITS = {
    "trial": {"verifications_per_period": 100, "period_days": 7},
    "basic": {"verifications_per_period": 1000, "period_days": 30},
    "pro": {"verifications_per_period": 10000, "period_days": 30},
    "enterprise": {"verifications_per_period": None, "period_days": 30},
    "unlimited": {"verifications_per_period": None, "period_days": 30},
}


async def get_tier_permissions(db: AsyncSession, tier_code: str) -> list[str]:
    """Get permissions for a tier from database or defaults."""
    try:
        result = await db.execute(
            text("SELECT permissions FROM dataintel_tiers WHERE tier_code = :tier"),
            {"tier": tier_code}
        )
        row = result.fetchone()
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.warning("Failed to get tier permissions from DB", error=str(e))

    # Fallback to defaults
    default = DEFAULT_TIER_PERMISSIONS.get(tier_code, [])
    return [p.value if isinstance(p, Permission) else p for p in default]


async def get_tier_limits(db: AsyncSession, tier_code: str) -> dict:
    """Get rate limits for a tier from database or defaults."""
    try:
        result = await db.execute(
            text("""
                SELECT verifications_per_period, period_days
                FROM dataintel_tiers WHERE tier_code = :tier
            """),
            {"tier": tier_code}
        )
        row = result.fetchone()
        if row:
            return {
                "verifications_per_period": row[0],
                "period_days": row[1] or 30
            }
    except Exception as e:
        logger.warning("Failed to get tier limits from DB", error=str(e))

    return DEFAULT_TIER_LIMITS.get(tier_code, DEFAULT_TIER_LIMITS["basic"])


def has_permission(user_permissions: list[str], required: Permission | str) -> bool:
    """Check if user has the required permission."""
    required_str = required.value if isinstance(required, Permission) else required
    return required_str in user_permissions


def check_permission(user_permissions: list[str], required: Permission | str) -> None:
    """Check permission and raise HTTPException if not granted."""
    from fastapi import HTTPException, status

    if not has_permission(user_permissions, required):
        required_str = required.value if isinstance(required, Permission) else required
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": f"Permission denied: {required_str} required",
                "code": "PERMISSION_DENIED",
                "required_permission": required_str,
                "upgrade_url": "https://unicorncommander.ai/plans"
            }
        )
