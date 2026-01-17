"""
Subscription status checking for Data Intel.

Integrates with Keycloak user attributes and Lago billing.
"""

import httpx
import structlog
from typing import Optional
from dataclasses import dataclass

from app.core.config import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class SubscriptionInfo:
    """User subscription information."""
    user_id: str
    tier: str
    status: str  # active, expired, cancelled, trial
    is_active: bool
    expires_at: Optional[str] = None


async def get_subscription_from_keycloak(user_id: str) -> Optional[SubscriptionInfo]:
    """
    Get subscription info from Keycloak user attributes.

    Keycloak stores: subscription_tier, subscription_status
    """
    settings = get_settings()

    if not settings.KEYCLOAK_ADMIN_URL:
        logger.warning("Keycloak admin URL not configured")
        return None

    try:
        async with httpx.AsyncClient() as client:
            # Get admin token
            token_response = await client.post(
                f"{settings.KEYCLOAK_ADMIN_URL}/realms/master/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": "admin-cli",
                    "client_secret": settings.KEYCLOAK_ADMIN_SECRET,
                }
            )

            if token_response.status_code != 200:
                logger.error("Failed to get Keycloak admin token")
                return None

            token = token_response.json()["access_token"]

            # Get user attributes
            user_response = await client.get(
                f"{settings.KEYCLOAK_ADMIN_URL}/admin/realms/uchub/users/{user_id}",
                headers={"Authorization": f"Bearer {token}"}
            )

            if user_response.status_code != 200:
                logger.warning("User not found in Keycloak", user_id=user_id)
                return None

            user_data = user_response.json()
            attributes = user_data.get("attributes", {})

            tier = attributes.get("subscription_tier", ["trial"])[0]
            status = attributes.get("subscription_status", ["active"])[0]

            return SubscriptionInfo(
                user_id=user_id,
                tier=tier,
                status=status,
                is_active=status in ["active", "trial"],
            )

    except Exception as e:
        logger.error("Failed to get subscription from Keycloak", error=str(e))
        return None


async def get_subscription_from_lago(user_id: str) -> Optional[SubscriptionInfo]:
    """
    Get subscription info from Lago billing system.

    Lago customer external_id = Keycloak user_id
    """
    settings = get_settings()

    if not settings.LAGO_API_URL or not settings.LAGO_API_KEY:
        logger.debug("Lago not configured")
        return None

    try:
        async with httpx.AsyncClient() as client:
            # Get customer subscriptions
            response = await client.get(
                f"{settings.LAGO_API_URL}/api/v1/subscriptions",
                params={"external_customer_id": user_id},
                headers={"Authorization": f"Bearer {settings.LAGO_API_KEY}"}
            )

            if response.status_code != 200:
                return None

            data = response.json()
            subscriptions = data.get("subscriptions", [])

            # Find active subscription
            for sub in subscriptions:
                if sub.get("status") == "active":
                    plan_code = sub.get("plan_code", "trial")
                    return SubscriptionInfo(
                        user_id=user_id,
                        tier=plan_code,
                        status="active",
                        is_active=True,
                        expires_at=sub.get("ending_at"),
                    )

            return SubscriptionInfo(
                user_id=user_id,
                tier="expired",
                status="expired",
                is_active=False,
            )

    except Exception as e:
        logger.error("Failed to get subscription from Lago", error=str(e))
        return None


async def get_user_subscription(user_id: str) -> SubscriptionInfo:
    """
    Get user subscription from available sources.

    Priority: Lago (billing source of truth) > Keycloak > Default
    """
    # Try Lago first (source of truth for billing)
    sub = await get_subscription_from_lago(user_id)
    if sub:
        return sub

    # Try Keycloak
    sub = await get_subscription_from_keycloak(user_id)
    if sub:
        return sub

    # Default to trial
    logger.warning("No subscription found, defaulting to trial", user_id=user_id)
    return SubscriptionInfo(
        user_id=user_id,
        tier="trial",
        status="trial",
        is_active=True,
    )
