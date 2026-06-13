"""GDPR Article 15 / Article 20 data export (subject + tenant).

This service gathers a person's (or an entire tenant's) operational record
into a single structured, machine-readable JSON document in an ICO / Art. 20
data-portability style, then makes it available to the requester:

* **Garage (production):** the document is PUT to the tenant's object store
  under a ``compliance-exports/`` prefix, and a short-lived presigned GET URL
  (TTL = ``settings.EXPORT_SIGNED_URL_TTL_SECONDS``) is returned as
  ``{"download_url": ..., "expires_at": ...}``.
* **Local / unconfigured / hiccup (fallback):** the document is returned
  *inline* as ``{"inline": <doc>}``. Subject exports are small enough to
  inline comfortably; tenant exports may be large, so a ``warning`` /
  ``note`` is attached and the caller (a Celery job) is expected to handle
  large inline payloads or persist them itself.

DORMANT-friendly: this module makes no network calls unless a real Garage
backend is configured (``StorageService.from_settings`` selects the local-fs
backend automatically in STANDALONE / dev / no-key environments). It never
raises on a storage problem — any upload/presign failure degrades to the
inline fallback with a structured ``warning``.

Every query runs under the caller-provided ``db`` AsyncSession, which the
caller has already RLS-bound to ``tenant_id`` via
``core.database.bind_session_context`` (the runtime role is NOBYPASSRLS, so
RLS confines every read to the bound tenant). This service issues read-only
SELECTs; it performs no mutation, so it is safe to run from a read replica or
a background job.

Settings referenced (all have safe-off / safe defaults — see Settings):
* ``EXPORT_SIGNED_URL_TTL_SECONDS`` — presigned GET URL lifetime.
* ``EXPORT_BUCKET_SUFFIX`` — bucket-name suffix appended to the tenant's
  ``garage_bucket_prefix`` (default ``"exports"``).
* (indirectly, via ``StorageService.from_settings``) ``STANDALONE_MODE`` /
  ``ENV`` / ``GARAGE_ACCESS_KEY`` / ``GARAGE_SECRET_KEY`` / ``GARAGE_ENDPOINT``
  decide local-fs vs Garage.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:  # pragma: no cover - typing only
    from contact_ops.core.config import Settings

logger = structlog.get_logger(__name__)

# Schema/version stamp embedded in every export document. Bump when the export
# shape changes so downstream consumers (and a re-import path) can branch.
EXPORT_SCHEMA_VERSION = "contact-ops.gdpr-export/1"

# Default TTL for the presigned GET URL if the Settings field is somehow absent
# (defensive: the field has its own default, this is a belt-and-braces floor).
_DEFAULT_SIGNED_URL_TTL_SECONDS = 3600
_DEFAULT_BUCKET_SUFFIX = "exports"


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _json_default(obj: Any) -> Any:
    """Serialize the value types that show up in our rows.

    asyncpg/SQLAlchemy hands back ``uuid.UUID``, ``datetime``/``date``,
    ``Decimal`` (via Numeric), ``bytes`` (BYTEA: content_hash, sha256), and
    ``memoryview``. JSONB columns already arrive as dict/list. Everything else
    falls back to ``str()`` so a serialization edge never raises mid-export.
    """
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        # Hash chains / sha256 — hex is the portable, human-diffable form.
        return bytes(obj).hex()
    # Decimal, and anything exotic.
    return str(obj)


def _rows(result: Any) -> list[dict[str, Any]]:
    """Materialize a Core result into a list of plain JSON-able dicts."""
    return [dict(row) for row in result.mappings().all()]


def _doc_bytes(doc: dict[str, Any]) -> bytes:
    """Stable, compact UTF-8 encoding of an export document."""
    return json.dumps(
        doc, sort_keys=True, default=_json_default, separators=(",", ":")
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Row gathering
# ---------------------------------------------------------------------------


async def _gather_person_record(
    db: AsyncSession, tenant_id: uuid.UUID, person_id: uuid.UUID
) -> dict[str, Any]:
    """Collect one person + every related table into a portability record.

    All queries are RLS-confined to the bound tenant. Each child table is
    keyed off ``person_id`` (or its text form / subject filters for the
    no-FK / polymorphic tables). The subject's ``action_event`` history is
    included verbatim (payload + actor JSONB) per Art. 15(1) "all information".
    """
    pid = str(person_id)
    params = {"pid": pid}

    # The person row itself (RLS already scopes to tenant; the explicit
    # canonical_owner_tenant_id guard is belt-and-braces against any future
    # cross-tenant visibility policy).
    person_result = await db.execute(
        text(
            """
            SELECT *
            FROM persons
            WHERE id = CAST(:pid AS uuid)
              AND canonical_owner_tenant_id = CAST(:tid AS uuid)
            """
        ),
        {"pid": pid, "tid": str(tenant_id)},
    )
    person_rows = _rows(person_result)
    person = person_rows[0] if person_rows else None

    async def q(sql: str) -> list[dict[str, Any]]:
        return _rows(await db.execute(text(sql), params))

    related: dict[str, list[dict[str, Any]]] = {}

    # CASCADE children — direct person_id FK.
    related["emails"] = await q(
        "SELECT * FROM emails WHERE person_id = CAST(:pid AS uuid) ORDER BY created_at"
    )
    related["phones"] = await q(
        "SELECT * FROM phones WHERE person_id = CAST(:pid AS uuid) ORDER BY created_at"
    )
    related["postal_addresses"] = await q(
        "SELECT * FROM postal_addresses WHERE person_id = CAST(:pid AS uuid) ORDER BY created_at"
    )
    related["identifiers"] = await q(
        "SELECT * FROM identifiers WHERE person_id = CAST(:pid AS uuid) ORDER BY created_at"
    )
    related["im_handles"] = await q(
        "SELECT * FROM im_handles WHERE person_id = CAST(:pid AS uuid) ORDER BY created_at"
    )
    related["urls"] = await q(
        "SELECT * FROM urls WHERE person_id = CAST(:pid AS uuid) ORDER BY created_at"
    )
    related["person_org_role"] = await q(
        "SELECT * FROM person_org_role WHERE person_id = CAST(:pid AS uuid) ORDER BY created_at"
    )
    related["person_aliases"] = await q(
        "SELECT * FROM person_alias WHERE current_canonical_id = CAST(:pid AS uuid) ORDER BY created_at"
    )

    # person-person relations: the subject can be on either side.
    related["person_person_relations"] = await q(
        """
        SELECT * FROM person_person_relation
        WHERE from_person_id = CAST(:pid AS uuid)
           OR to_person_id = CAST(:pid AS uuid)
        ORDER BY created_at
        """
    )

    # photos + their media-asset metadata (object_key/bucket/sha256/size, never
    # the binary itself — the data subject can re-fetch via their photo URLs).
    related["photos"] = await q(
        """
        SELECT ph.*, ma.bucket AS asset_bucket, ma.object_key AS asset_object_key,
               ma.content_type AS asset_content_type, ma.byte_size AS asset_byte_size,
               ma.sha256 AS asset_sha256, ma.kind AS asset_kind,
               ma.captured_at AS asset_captured_at
        FROM photos ph
        LEFT JOIN media_assets ma ON ma.id = ph.asset_id
        WHERE ph.person_id = CAST(:pid AS uuid)
        ORDER BY ph.created_at
        """
    )

    # Voice consent ledger + voice-sample metadata (embeddings excluded — they
    # are derived biometric vectors, not source data; sample provenance is the
    # portable part).
    related["voice_consent"] = await q(
        "SELECT * FROM voice_consent WHERE person_id = CAST(:pid AS uuid) ORDER BY created_at"
    )
    related["voice_samples"] = await q(
        """
        SELECT id, asset_id, person_id, voice_fingerprint_id, embedding_model,
               duration_seconds, quality_score, snr_db, captured_at, meeting_id,
               speaker_label, source_id, created_at
        FROM voice_samples
        WHERE person_id = CAST(:pid AS uuid)
        ORDER BY created_at
        """
    )

    # No-FK / polymorphic tables — filter by the subject explicitly.
    related["facts"] = await q(
        """
        SELECT * FROM facts
        WHERE subject_kind = 'person' AND subject_id = CAST(:pid AS uuid)
        ORDER BY created_at
        """
    )
    related["notes"] = await q(
        """
        SELECT * FROM notes
        WHERE target_type = 'person' AND target_id = :pid
        ORDER BY created_at
        """
    )
    related["field_provenance"] = await q(
        """
        SELECT * FROM field_provenance
        WHERE entity_type = 'person' AND entity_id = CAST(:pid AS uuid)
        ORDER BY established_at
        """
    )
    related["merge_history"] = await q(
        """
        SELECT * FROM merge_history
        WHERE entity_type = 'person'
          AND (kept_id = CAST(:pid AS uuid) OR removed_id = CAST(:pid AS uuid))
        ORDER BY performed_at
        """
    )

    # The subject's own audit history. Include the full hash-chained event
    # (payload + actor JSONB) so the export is complete per Art. 15(1).
    related["action_events"] = await q(
        """
        SELECT * FROM action_event
        WHERE aggregate_id = CAST(:pid AS uuid)
           OR affected_ids @> ARRAY[CAST(:pid AS uuid)]
        ORDER BY proposed_at
        """
    )

    return {"person": person, "related": related}


# ---------------------------------------------------------------------------
# Storage delivery
# ---------------------------------------------------------------------------


async def _tenant_bucket_prefix(
    db: AsyncSession, tenant_id: uuid.UUID
) -> str | None:
    """Read the tenant's ``garage_bucket_prefix`` under RLS.

    Returns None if the tenant row is not visible (it always should be under a
    correctly-bound session) so the caller falls back to inline.
    """
    prefix = await db.scalar(
        text(
            "SELECT garage_bucket_prefix FROM tenants WHERE id = CAST(:tid AS uuid)"
        ),
        {"tid": str(tenant_id)},
    )
    return prefix or None


def _settings_int(settings: Any, name: str, default: int) -> int:
    value = getattr(settings, name, None)
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


async def _deliver(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    doc: dict[str, Any],
    *,
    settings: "Settings",
    key_hint: str,
) -> dict[str, Any]:
    """Upload ``doc`` to Garage and presign a GET, else fall back to inline.

    Never raises: any storage problem (unconfigured backend, network hiccup,
    missing bucket prefix) downgrades to ``{"inline": doc, "warning": ...}``.
    """
    # Lazy import keeps this module importable without the storage stack and
    # avoids constructing the singleton in pure-DB unit tests.
    try:
        from contact_ops.services.storage import (
            LocalFilesystemBackend,
            StorageService,
        )

        storage = StorageService.from_settings(settings)
    except Exception:  # pragma: no cover - storage construction is defensive
        logger.warning("export_storage_init_failed", tenant_id=str(tenant_id), exc_info=True)
        return {"inline": doc, "warning": "storage_unavailable_inline_fallback"}

    backend = getattr(storage, "_backend", None)
    is_local = isinstance(backend, LocalFilesystemBackend)

    prefix = await _tenant_bucket_prefix(db, tenant_id)
    if is_local or not prefix:
        # Local-fs presigned URLs are file:// (not shareable) and a missing
        # prefix means we cannot scope a bucket — inline is the correct answer.
        reason = (
            "storage_local_backend_inline_fallback"
            if is_local
            else "tenant_bucket_prefix_missing_inline_fallback"
        )
        return {"inline": doc, "warning": reason}

    suffix = getattr(settings, "EXPORT_BUCKET_SUFFIX", _DEFAULT_BUCKET_SUFFIX) or _DEFAULT_BUCKET_SUFFIX
    bucket = f"{prefix}-{suffix}"
    now = _dt.datetime.now(tz=_dt.UTC)
    key = (
        f"compliance-exports/{key_hint}/"
        f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}.json"
    )
    ttl = _settings_int(
        settings, "EXPORT_SIGNED_URL_TTL_SECONDS", _DEFAULT_SIGNED_URL_TTL_SECONDS
    )

    try:
        await storage.put_bytes(
            bucket=bucket,
            key=key,
            body=_doc_bytes(doc),
            content_type="application/json",
        )
        download_url = await storage.presigned_get_url(
            bucket=bucket, key=key, expires_seconds=ttl
        )
    except Exception:
        logger.warning(
            "export_upload_failed_inline_fallback",
            tenant_id=str(tenant_id),
            bucket=bucket,
            key=key,
            exc_info=True,
        )
        return {"inline": doc, "warning": "storage_upload_failed_inline_fallback"}

    expires_at = (now + _dt.timedelta(seconds=ttl)).isoformat()
    logger.info(
        "export_uploaded",
        tenant_id=str(tenant_id),
        bucket=bucket,
        key=key,
        expires_at=expires_at,
    )
    return {
        "download_url": download_url,
        "expires_at": expires_at,
        "bucket": bucket,
        "object_key": key,
    }


# ---------------------------------------------------------------------------
# Public contract
# ---------------------------------------------------------------------------


async def export_subject_data(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    person_id: uuid.UUID,
    *,
    settings: "Settings",
) -> dict[str, Any]:
    """GDPR Art. 15 / Art. 20 export for a single data subject.

    Gathers the person + every related table into a portability document and
    delivers it. Returns either ``{"download_url", "expires_at", ...}`` (Garage)
    or ``{"inline": <doc>, "warning": ...}`` (local/unconfigured/hiccup).

    ``db`` MUST already be RLS-bound to ``tenant_id``. Read-only; safe in a job.
    """
    generated_at = _dt.datetime.now(tz=_dt.UTC).isoformat()
    record = await _gather_person_record(db, tenant_id, person_id)

    not_found = record.get("person") is None
    counts = {table: len(rows) for table, rows in record["related"].items()}

    doc: dict[str, Any] = {
        "schema": EXPORT_SCHEMA_VERSION,
        "export_kind": "subject",
        "generated_at": generated_at,
        "tenant_id": str(tenant_id),
        "subject": {
            "person_id": str(person_id),
            "not_found": not_found,
        },
        "person": record["person"],
        "related": record["related"],
        "summary": {"related_counts": counts},
        "legal_basis": "GDPR Art.15 right of access / Art.20 data portability",
        "notes": [
            "Biometric vectors (voice/face embeddings) are excluded as derived "
            "data; their provenance metadata is included.",
            "action_event content_hash/prev_event_hash are hex-encoded; they are "
            "the tamper-evident audit chain, not personal data.",
        ],
    }

    logger.info(
        "export_subject_built",
        tenant_id=str(tenant_id),
        person_id=str(person_id),
        not_found=not_found,
        related_counts=counts,
    )
    return await _deliver(
        db, tenant_id, doc, settings=settings, key_hint=f"person/{person_id}"
    )


async def export_tenant_data(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    settings: "Settings",
) -> dict[str, Any]:
    """GDPR export for an ENTIRE tenant (every person + the tenant record).

    Iterates each visible person and reuses the subject gatherer, then attaches
    the tenant row itself. May be large; when it falls back to inline a
    ``note`` flags that the caller should persist it rather than returning it
    over the wire.

    ``db`` MUST already be RLS-bound to ``tenant_id``. Read-only.
    """
    generated_at = _dt.datetime.now(tz=_dt.UTC).isoformat()

    tenant_result = await db.execute(
        text("SELECT * FROM tenants WHERE id = CAST(:tid AS uuid)"),
        {"tid": str(tenant_id)},
    )
    tenant_rows = _rows(tenant_result)
    tenant_row = tenant_rows[0] if tenant_rows else None

    id_result = await db.execute(
        text(
            """
            SELECT id FROM persons
            WHERE canonical_owner_tenant_id = CAST(:tid AS uuid)
            ORDER BY created_at
            """
        ),
        {"tid": str(tenant_id)},
    )
    person_ids = [row[0] for row in id_result.all()]

    subjects: list[dict[str, Any]] = []
    for pid in person_ids:
        pid_uuid = pid if isinstance(pid, uuid.UUID) else uuid.UUID(str(pid))
        record = await _gather_person_record(db, tenant_id, pid_uuid)
        subjects.append(
            {
                "person_id": str(pid_uuid),
                "person": record["person"],
                "related": record["related"],
            }
        )

    doc: dict[str, Any] = {
        "schema": EXPORT_SCHEMA_VERSION,
        "export_kind": "tenant",
        "generated_at": generated_at,
        "tenant_id": str(tenant_id),
        "tenant": tenant_row,
        "subjects": subjects,
        "summary": {"person_count": len(subjects)},
        "legal_basis": "GDPR Art.15 right of access / Art.20 data portability",
    }

    logger.info(
        "export_tenant_built",
        tenant_id=str(tenant_id),
        person_count=len(subjects),
    )
    result = await _deliver(
        db, tenant_id, doc, settings=settings, key_hint=f"tenant/{tenant_id}"
    )
    if "inline" in result:
        result.setdefault(
            "note",
            "tenant export returned inline; payload may be large — persist it "
            "rather than returning it over the wire",
        )
    return result


__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "export_subject_data",
    "export_tenant_data",
]
