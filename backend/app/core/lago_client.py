"""
Lago billing integration for Center Deep Verify.

Sends usage events to Lago for metered billing.
"""

import uuid
from datetime import datetime
from typing import Optional

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)


class LagoClient:
    """Client for Lago billing API."""

    def __init__(self):
        settings = get_settings()
        self.api_url = settings.LAGO_API_URL
        self.api_key = settings.LAGO_API_KEY
        self.enabled = bool(self.api_url and self.api_key)

        if not self.enabled:
            logger.warning("Lago not configured - billing events will not be sent")

    async def send_event(
        self,
        external_customer_id: str,
        code: str,
        transaction_id: Optional[str] = None,
        properties: Optional[dict] = None,
    ) -> bool:
        """
        Send a usage event to Lago.

        Args:
            external_customer_id: Customer ID (user_id from Keycloak)
            code: Billable metric code (e.g., 'email_verifications')
            transaction_id: Unique transaction ID for idempotency
            properties: Additional properties for the event

        Returns:
            True if event was sent successfully, False otherwise
        """
        if not self.enabled:
            logger.debug("Lago disabled, skipping event", code=code)
            return False

        if not transaction_id:
            transaction_id = str(uuid.uuid4())

        event_data = {
            "event": {
                "transaction_id": transaction_id,
                "external_customer_id": external_customer_id,
                "code": code,
                "timestamp": int(datetime.utcnow().timestamp()),
                "properties": properties or {}
            }
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.api_url}/api/v1/events",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=event_data,
                )

                if response.status_code in (200, 201):
                    logger.debug(
                        "Lago event sent",
                        code=code,
                        customer=external_customer_id,
                        transaction_id=transaction_id,
                    )
                    return True
                else:
                    logger.error(
                        "Lago event failed",
                        status=response.status_code,
                        response=response.text[:200],
                        code=code,
                    )
                    return False

        except Exception as e:
            logger.error("Lago event error", error=str(e), code=code)
            return False

    async def send_verification_event(
        self,
        user_id: str,
        verification_id: str,
        email: Optional[str] = None,
        count: int = 1,
    ) -> bool:
        """
        Send email verification usage event to Lago.

        Args:
            user_id: User ID (external_customer_id in Lago)
            verification_id: Unique ID for this verification
            email: Email that was verified (for properties)
            count: Number of verifications (for batch)

        Returns:
            True if event was sent successfully
        """
        properties = {
            "verification_id": verification_id,
        }

        if email:
            properties["email_domain"] = email.split("@")[-1] if "@" in email else None

        # For batch, send multiple events or one with count
        # Lago counts events, so we send one event per verification
        # For efficiency with large batches, we can send count in properties
        # and configure Lago metric to sum a property instead of counting

        return await self.send_event(
            external_customer_id=user_id,
            code="email_verifications",
            transaction_id=verification_id,
            properties=properties,
        )


# Singleton instance
_lago_client: Optional[LagoClient] = None


def get_lago_client() -> LagoClient:
    """Get or create Lago client singleton."""
    global _lago_client
    if _lago_client is None:
        _lago_client = LagoClient()
    return _lago_client
