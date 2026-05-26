"""Webhook signature verification and cache invalidation helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

from contact_ops.federation.consumer_sdk.cache import ContactCache
from contact_ops.federation.consumer_sdk.models import WebhookEvent


def sign_payload(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class WebhookVerifier:
    def __init__(self, secret: str) -> None:
        self._secret = secret

    def verify(self, body: bytes, signature: str) -> WebhookEvent:
        expected = sign_payload(body, self._secret)
        if not hmac.compare_digest(expected, signature):
            raise ValueError("invalid Contact-Ops webhook signature")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("webhook payload must be a JSON object")
        return WebhookEvent.model_validate(payload)


def invalidate_cache_for_event(cache: ContactCache, event: WebhookEvent) -> None:
    cache.invalidate_entity(str(event.aggregate_id))
    for value in event.payload.get("affected_ids", []):
        try:
            cache.invalidate_entity(str(uuid.UUID(str(value))))
        except ValueError:
            continue


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

