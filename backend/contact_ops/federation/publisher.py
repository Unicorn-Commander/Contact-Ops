"""Best-effort webhook publisher for Contact-Ops action_event changes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.core.config import get_settings
from contact_ops.federation.consumer_sdk.webhooks import canonical_json, sign_payload
from contact_ops.security.ssrf import check_outbound

PUBLISHABLE_AGGREGATES = {"person", "organization", "edge"}


class WebhookPublishResult(dict[str, int]):
    """Dictionary with `attempted`, `delivered`, and `failed` counts."""


def build_webhook_payload(row: dict[str, Any]) -> dict[str, Any]:
    occurred_at = row.get("applied_at") or row.get("proposed_at") or datetime.now(UTC)
    if isinstance(occurred_at, datetime):
        occurred_at = occurred_at.isoformat()
    return {
        "event_id": str(row["event_id"]),
        "event_type": str(row["event_type"]),
        "aggregate_type": str(row["aggregate_type"]),
        "aggregate_id": str(row["aggregate_id"]),
        "tenant_id": str(row["tenant_id"]),
        "occurred_at": occurred_at,
        "payload": {
            "affected_ids": [str(value) for value in row.get("affected_ids", [])],
            "event_payload": row.get("payload", {}),
        },
    }


async def publish_action_event(
    db: AsyncSession,
    *,
    action_event_id: uuid.UUID,
    http: httpx.AsyncClient | None = None,
) -> WebhookPublishResult:
    event_result = await db.execute(
        text(
            """
            SELECT event_id, event_type, tenant_id, aggregate_type, aggregate_id,
                   affected_ids, payload, proposed_at, applied_at
            FROM action_event
            WHERE event_id = :event_id
            """
        ),
        {"event_id": action_event_id},
    )
    event = event_result.mappings().one_or_none()
    if event is None:
        return WebhookPublishResult(attempted=0, delivered=0, failed=0)
    if str(event["aggregate_type"]) not in PUBLISHABLE_AGGREGATES:
        return WebhookPublishResult(attempted=0, delivered=0, failed=0)

    subscriptions_result = await db.execute(
        text(
            """
            SELECT consumer_app_id, url, hmac_secret
            FROM consumer_webhook_subscription
            WHERE tenant_id = :tenant_id
              AND active IS TRUE
              AND event_kinds @> ARRAY[:event_type]::text[]
            ORDER BY consumer_app_id
            """
        ),
        {"tenant_id": event["tenant_id"], "event_type": event["event_type"]},
    )
    subscriptions = subscriptions_result.mappings().all()
    payload = build_webhook_payload(dict(event))
    body = canonical_json(payload)
    own_client = http is None
    client = http or httpx.AsyncClient(timeout=5.0)
    delivered = 0
    failed = 0
    settings = get_settings()
    try:
        for subscription in subscriptions:
            sub_url = str(subscription["url"])
            # SSRF guard: consumer-supplied webhook URLs are an egress vector.
            # Dormant/shadow by default (check_outbound returns allowed=True +
            # logs ssrf_would_block); only refuses once the guard is enforced.
            allowed, _ssrf_reason = await check_outbound(sub_url, settings)
            if not allowed:
                failed += 1
                continue
            signature = sign_payload(body, str(subscription["hmac_secret"]))
            try:
                response = await client.post(
                    sub_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Contact-Ops-Signature": signature,
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError:
                failed += 1
            else:
                delivered += 1
                await db.execute(
                    text(
                        """
                        UPDATE consumer_webhook_subscription
                        SET last_delivered_at = now()
                        WHERE consumer_app_id = :consumer_app_id
                          AND tenant_id = :tenant_id
                        """
                    ),
                    {
                        "consumer_app_id": subscription["consumer_app_id"],
                        "tenant_id": event["tenant_id"],
                    },
                )
    finally:
        if own_client:
            await client.aclose()
    return WebhookPublishResult(
        attempted=len(subscriptions),
        delivered=delivered,
        failed=failed,
    )
