"""Small TTL cache for consumer display lookups."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from contact_ops.federation.consumer_sdk.models import CacheRecord


class ContactCache:
    """In-memory cache with optional JSON-file persistence.

    Consumer apps use this only for display hints. Contact-Ops remains the
    source of truth and webhook invalidation clears affected entries.
    """

    def __init__(self, path: Path | None = None, ttl_seconds: int = 300) -> None:
        self._path = path
        self._ttl_seconds = ttl_seconds
        self._records: dict[str, CacheRecord] = {}
        if path is not None:
            self._load()

    def get(self, key: str) -> dict[str, Any] | None:
        record = self._records.get(key)
        if record is None:
            return None
        if record.expires_at <= time.monotonic():
            self._records.pop(key, None)
            self._persist()
            return None
        return record.value

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._records[key] = CacheRecord(
            value=value,
            expires_at=time.monotonic() + self._ttl_seconds,
        )
        self._persist()

    def invalidate(self, key: str) -> None:
        self._records.pop(key, None)
        self._persist()

    def invalidate_entity(self, entity_id: str) -> None:
        suffix = f":{entity_id}"
        for key in list(self._records):
            if key == entity_id or key.endswith(suffix):
                self._records.pop(key, None)
        self._persist()

    def clear(self) -> None:
        self._records.clear()
        self._persist()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return
        self._records = {
            key: CacheRecord.model_validate(value)
            for key, value in raw.items()
            if isinstance(value, dict)
        }

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {key: record.model_dump(mode="json") for key, record in self._records.items()}
        self._path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

