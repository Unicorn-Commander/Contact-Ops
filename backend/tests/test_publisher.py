from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.federation.publisher import publish_action_event


@pytest.mark.asyncio
async def test_publish_action_event_posts_signed_payload(db_session: AsyncSession) -> None:
    tenant_id = uuid.UUID(
        str(
            (
                await db_session.execute(
                    text("SELECT id FROM tenants WHERE slug = 'magic-unicorn-llc'")
                )
            ).scalar_one()
        )
    )
    event_id = uuid.UUID("00000000-0000-0000-0000-000000000301")
    person_id = uuid.UUID("00000000-0000-0000-0000-000000000302")
    seen: list[httpx.Request] = []

    await db_session.execute(
        text(
            """
            INSERT INTO consumer_webhook_subscription (
                consumer_app_id, tenant_id, url, event_kinds, hmac_secret
            )
            VALUES (
                'listing-ops',
                :tenant_id,
                'https://listing.example.test/contact-ops/webhook',
                ARRAY['person.applied']::text[],
                'webhook-test-value'
            )
            """
        ),
        {"tenant_id": tenant_id},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO action_event (
                event_id, event_type, tenant_id, aggregate_type, aggregate_id,
                affected_ids, payload, actor, actor_type, status, content_hash
            )
            VALUES (
                :event_id,
                'person.applied',
                :tenant_id,
                'person',
                :person_id,
                ARRAY[:person_id]::uuid[],
                '{"display_name":"Daniel Tran"}'::jsonb,
                '{"sub":"migration"}'::jsonb,
                'migration',
                'applied',
                decode('00', 'hex')
            )
            """
        ),
        {"event_id": event_id, "tenant_id": tenant_id, "person_id": person_id},
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    result = await publish_action_event(
        db_session,
        action_event_id=event_id,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert result == {"attempted": 1, "delivered": 1, "failed": 0}
    assert len(seen) == 1
    assert seen[0].headers["x-contact-ops-signature"].startswith("sha256=")
