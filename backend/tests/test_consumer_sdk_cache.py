from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from contact_ops.federation.consumer_sdk.cache import ContactCache
from contact_ops.federation.consumer_sdk.models import WebhookEvent
from contact_ops.federation.consumer_sdk.webhooks import (
    WebhookVerifier,
    canonical_json,
    invalidate_cache_for_event,
    sign_payload,
)


def test_cache_ttl_and_persistence(tmp_path: Path) -> None:
    path = tmp_path / "contact-cache.json"
    cache = ContactCache(path=path, ttl_seconds=300)
    person_id = uuid.UUID("00000000-0000-0000-0000-000000000201")
    cache.set(f"person:{person_id}", {"display_name": "Daniel Tran"})

    restored = ContactCache(path=path, ttl_seconds=300)

    assert restored.get(f"person:{person_id}") == {"display_name": "Daniel Tran"}
    assert json.loads(path.read_text(encoding="utf-8"))


def test_cache_invalidation_from_signed_webhook() -> None:
    person_id = uuid.UUID("00000000-0000-0000-0000-000000000202")
    cache = ContactCache(ttl_seconds=300)
    cache.set(f"person:{person_id}", {"display_name": "Old Name"})
    body = canonical_json(
        {
            "event_id": "00000000-0000-0000-0000-000000000203",
            "event_type": "person.applied",
            "aggregate_type": "person",
            "aggregate_id": str(person_id),
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "occurred_at": "2026-05-26T12:00:00Z",
            "payload": {},
        }
    )
    verifier = WebhookVerifier("webhook-test-value")

    event = verifier.verify(body, sign_payload(body, "webhook-test-value"))
    invalidate_cache_for_event(cache, event)

    assert isinstance(event, WebhookEvent)
    assert cache.get(f"person:{person_id}") is None


def test_cache_expiry() -> None:
    cache = ContactCache(ttl_seconds=0)
    cache.set("person:expired", {"display_name": "Expired"})

    assert cache.get("person:expired") is None
