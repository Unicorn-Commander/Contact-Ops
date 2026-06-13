"""GDPR Art.17 erasure (right to be forgotten) for Contact-Ops.

DESIGN (Aaron, 2026-06): erasure HARD-DELETES operational PII but
ANONYMIZES the hash-chained ``action_event`` ledger IN PLACE (ledger rows are
never deleted — migration 0038 grants UPDATE, not DELETE, on ``action_event``).
Erasure is two-phase:

* :func:`tombstone_person` (phase 1) redacts the direct PII columns on the
  ``persons`` row and stamps ``tombstoned_at`` / ``purge_after`` (now + grace).
  Children are left intact; the person becomes a tombstone in a grace window.
* :func:`purge_person` (phase 2, run by the retention sweep after the grace
  window) pre-clears the no-cascade orphans, anonymizes the ledger, then
  DELETEs the person row so Postgres ``ON DELETE CASCADE`` removes the child
  PII rows. External stores (Garage / Qdrant / FalkorDB) are purged
  best-effort — a missing or unhealthy external store must never fail a purge.

HASH-CHAIN RULE (lawful Art.17 exception): when anonymizing a ledger event we
redact the PII inside ``actor`` / ``payload->before`` / ``payload->after`` but
DO NOT recompute ``content_hash`` and DO NOT touch ``prev_event_hash``. The
chain links survive; a redacted event intentionally won't re-verify its own
``content_hash`` — that is the documented, lawful exception.

Everything runs under the caller's RLS-bound :class:`AsyncSession` (the GUC
``app.tenant_id`` is already ``SET LOCAL`` via
``core.database.bind_session_context``); the runtime role is NOBYPASSRLS, so
every statement here is implicitly tenant-scoped. This module is DORMANT until
its config flags are flipped on (see ``COMPLIANCE_ERASURE_ENABLED``); callers
gate on the flag before invoking these helpers.
"""

from __future__ import annotations

import hashlib
import json
import uuid as uuid_module
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Direct PII columns on ``persons`` blanked by phase-1 tombstone. Scalar text
#: columns go to the ``[erased]`` sentinel where NOT NULL (display_name), the
#: rest go NULL. Array columns reset to empty; JSONB date columns go NULL.
_PII_SENTINEL = "[erased]"

#: Child tables removed by Postgres ``ON DELETE CASCADE`` when the person row is
#: deleted. Counted (per table) BEFORE the delete so the cascade_summary
#: reports real numbers (the rows are gone by the time we could re-count).
_CASCADE_CHILD_TABLES: tuple[tuple[str, str], ...] = (
    ("emails", "person_id"),
    ("phones", "person_id"),
    ("postal_addresses", "person_id"),
    ("identifiers", "person_id"),
    ("im_handles", "person_id"),
    ("urls", "person_id"),
    ("photos", "person_id"),
    ("person_org_role", "person_id"),
    ("person_alias", "current_canonical_id"),
    ("person_tenant_membership", "person_id"),
    ("voice_consent", "person_id"),
    ("voice_fingerprints", "person_id"),
)


# ---------------------------------------------------------------------------
# Ledger pseudonym + redaction helpers
# ---------------------------------------------------------------------------

#: JSONB keys treated as PII anywhere in actor / payload-before / payload-after.
#: Matched case-insensitively against leaf keys; values are replaced with the
#: per-person stable pseudonym so cross-referencing is broken but the event
#: shape (and therefore downstream readers) survive.
_PII_KEYS: frozenset[str] = frozenset(
    {
        "display_name",
        "given_name",
        "family_name",
        "additional_names",
        "honorific_prefix",
        "honorific_suffix",
        "phonetic_family_name",
        "phonetic_given_name",
        "nicknames",
        "file_as",
        "pronouns",
        "gender_identity",
        "birthday",
        "anniversary",
        "death_date",
        "headline",
        "bio",
        "occupation_title",
        "name",
        "full_name",
        "first_name",
        "last_name",
        "middle_name",
        "email",
        "email_address",
        "phone",
        "phone_number",
        "address",
        "street",
        "postal_code",
        "im_handle",
        "username",
        "handle",
        "url",
        "photo",
        "photo_url",
        "value",
        "raw_value",
    }
)


def _pseudonym(person_id: uuid_module.UUID | str) -> str:
    """Stable, non-reversible pseudonym for a person id (``erased:<8hex>``)."""
    digest = hashlib.sha256(str(person_id).encode("utf-8")).hexdigest()[:8]
    return f"erased:{digest}"


def _redact_json(value: Any, pseudonym: str) -> Any:
    """Recursively replace PII-keyed leaf values with the pseudonym.

    Operates on already-decoded JSON (dict/list/scalar). Any key whose
    lowercased name is in :data:`_PII_KEYS` has its entire subtree replaced by
    the pseudonym string; other containers are walked so nested PII is reached.
    Non-PII scalars are returned untouched.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _PII_KEYS:
                out[k] = pseudonym
            else:
                out[k] = _redact_json(v, pseudonym)
        return out
    if isinstance(value, list):
        return [_redact_json(item, pseudonym) for item in value]
    return value


# ---------------------------------------------------------------------------
# Phase 1 — tombstone
# ---------------------------------------------------------------------------


async def tombstone_person(
    db: AsyncSession,
    tenant_id: str,
    person_id: str,
    *,
    reason: str,
    grace_days: int,
) -> dict:
    """Phase-1 erasure: redact direct PII on the person row + stamp tombstone.

    Sets ``tombstoned_at = now()`` and ``purge_after = now() + grace_days`` and
    blanks the direct PII columns on the ``persons`` row. Does NOT delete
    children — the retention sweep purges those once ``purge_after`` passes.

    Returns ``{"tombstoned_at", "purge_after"}`` (ISO strings) plus echoed
    ``reason``. Raises nothing for a missing/cross-tenant person other than a
    ``ValueError`` (RLS already constrains visibility to the bound tenant).
    """
    now = datetime.now(UTC)
    purge_after = now + timedelta(days=max(0, int(grace_days)))

    result = await db.execute(
        text(
            """
            UPDATE persons
            SET display_name           = :sentinel,
                given_name             = NULL,
                family_name            = NULL,
                additional_names       = '{}',
                honorific_prefix       = NULL,
                honorific_suffix       = NULL,
                phonetic_family_name   = NULL,
                phonetic_given_name    = NULL,
                nicknames              = '{}',
                file_as                = NULL,
                pronouns               = NULL,
                gender_identity        = NULL,
                birthday               = NULL,
                anniversary            = NULL,
                death_date             = NULL,
                headline               = NULL,
                bio                    = NULL,
                occupation_title       = NULL,
                tombstoned_at          = :now,
                purge_after            = :purge_after,
                updated_at             = :now
            WHERE id = :pid
            RETURNING id
            """
        ),
        {
            "sentinel": _PII_SENTINEL,
            "now": now,
            "purge_after": purge_after,
            "pid": person_id,
        },
    )
    if result.first() is None:
        raise ValueError(f"person not found or not visible in tenant: {person_id}")

    logger.info(
        "compliance.tombstone_person",
        tenant_id=tenant_id,
        person_id=person_id,
        reason=reason,
        grace_days=grace_days,
        purge_after=purge_after.isoformat(),
    )

    await _emit_erase_event(
        db,
        tenant_id=tenant_id,
        event_type="person.tombstone",
        aggregate_id=person_id,
        scope={
            "phase": "tombstone",
            "reason": reason,
            "grace_days": grace_days,
            "purge_after": purge_after.isoformat(),
        },
    )

    return {
        "tombstoned_at": now.isoformat(),
        "purge_after": purge_after.isoformat(),
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Ledger anonymization
# ---------------------------------------------------------------------------


async def anonymize_subject_action_events(
    db: AsyncSession,
    tenant_id: str,
    person_id: str,
) -> int:
    """Redact PII inside the ledger events that reference ``person_id``.

    Loads every ``action_event`` for this tenant whose ``aggregate_id`` or
    ``affected_ids`` references the person, rewrites the ``actor`` and the
    ``payload`` (``before`` / ``after`` subtrees) with PII leaf values replaced
    by a stable pseudonym, and writes them back. ``content_hash`` and
    ``prev_event_hash`` are deliberately LEFT UNTOUCHED (hash-chain rule).

    Returns the number of ledger rows rewritten.
    """
    pseudonym = _pseudonym(person_id)

    rows = (
        await db.execute(
            text(
                """
                SELECT event_id, actor, payload
                FROM action_event
                WHERE tenant_id = :tid
                  AND (aggregate_id = :pid OR :pid = ANY(affected_ids))
                """
            ),
            {"tid": tenant_id, "pid": person_id},
        )
    ).all()

    count = 0
    for event_id, actor, payload in rows:
        new_actor = _redact_json(actor, pseudonym) if actor is not None else actor
        new_payload = _redact_json(payload, pseudonym) if payload is not None else payload

        # No-op events still get rewritten (cheap) — correctness over a
        # per-row diff; the redaction is idempotent.
        await db.execute(
            text(
                # Cast through text->jsonb so the json_serializer/default=str
                # quirks of the engine are bypassed; values are plain JSON here.
                """
                UPDATE action_event
                SET actor   = CAST(:actor AS jsonb),
                    payload = CAST(:payload AS jsonb)
                WHERE event_id = :eid
                """
            ),
            {
                "eid": event_id,
                "actor": json.dumps(new_actor, default=str),
                "payload": json.dumps(new_payload, default=str),
            },
        )
        count += 1

    logger.info(
        "compliance.anonymize_ledger",
        tenant_id=tenant_id,
        person_id=person_id,
        events_redacted=count,
    )
    return count


# ---------------------------------------------------------------------------
# Phase 2 — purge
# ---------------------------------------------------------------------------


async def purge_person(
    db: AsyncSession,
    tenant_id: str,
    person_id: str,
) -> dict:
    """Phase-2 erasure: hard-delete the person + orphans, anonymize ledger.

    Order (legal correctness):
      1. clear ``dedup_pair_candidates`` (FK RESTRICT — must pre-clear or the
         person delete errors);
      2. delete the no-cascade orphans (``facts`` / ``notes`` /
         ``merge_history`` / ``field_provenance``) with exact WHERE clauses,
         counting rows for the summary;
      3. anonymize the ledger events for this person;
      4. count the CASCADE child rows BEFORE deleting the person;
      5. delete the person row (Postgres cascades the children);
      6. best-effort external purge (Garage / Qdrant / FalkorDB) — every error
         is swallowed to a warning.

    Returns ``{"person_id", "cascade_summary": {table: count}, "ledger_anonymized",
    "external": {...}}`` with REAL counts.
    """
    pid_str = str(person_id)
    cascade_summary: dict[str, int] = {}

    # (1) Pre-clear the FK-RESTRICT orphan so the person delete can proceed.
    res = await db.execute(
        text(
            """
            DELETE FROM dedup_pair_candidates
            WHERE person_a_id = :pid OR person_b_id = :pid
            """
        ),
        {"pid": person_id},
    )
    cascade_summary["dedup_pair_candidates"] = res.rowcount or 0

    # (2) No-FK / no-cascade orphans — exact WHERE clauses per the schema.
    res = await db.execute(
        text(
            "DELETE FROM facts WHERE subject_kind = 'person' AND subject_id = :pid"
        ),
        {"pid": person_id},
    )
    cascade_summary["facts"] = res.rowcount or 0

    res = await db.execute(
        text(
            # notes.target_id is TEXT — bind the string form of the id.
            "DELETE FROM notes WHERE target_type = 'person' AND target_id = :pid_str"
        ),
        {"pid_str": pid_str},
    )
    cascade_summary["notes"] = res.rowcount or 0

    res = await db.execute(
        text(
            """
            DELETE FROM merge_history
            WHERE entity_type = 'person'
              AND (kept_id = :pid OR removed_id = :pid)
            """
        ),
        {"pid": person_id},
    )
    cascade_summary["merge_history"] = res.rowcount or 0

    res = await db.execute(
        text(
            """
            DELETE FROM field_provenance
            WHERE entity_type = 'person' AND entity_id = :pid
            """
        ),
        {"pid": person_id},
    )
    cascade_summary["field_provenance"] = res.rowcount or 0

    # (3) Anonymize the ledger BEFORE we lose the person row (and so the lawful
    # record of the erasure itself, emitted below, is never redacted).
    ledger_anonymized = await anonymize_subject_action_events(
        db, tenant_id, person_id
    )

    # (4) Count CASCADE children BEFORE deleting the person row.
    for table, fk_col in _CASCADE_CHILD_TABLES:
        cnt = await db.scalar(
            text(f"SELECT count(*) FROM {table} WHERE {fk_col} = :pid"),
            {"pid": person_id},
        )
        cascade_summary[table] = int(cnt or 0)

    # person_person_relation is a two-sided edge (subject + object person fk);
    # count both sides for the summary (CASCADE removes either-side rows).
    rel_cnt = await db.scalar(
        text(
            """
            SELECT count(*) FROM person_person_relation
            WHERE from_person_id = :pid OR to_person_id = :pid
            """
        ),
        {"pid": person_id},
    )
    cascade_summary["person_person_relation"] = int(rel_cnt or 0)

    # (5) Delete the person — Postgres ON DELETE CASCADE removes the children.
    res = await db.execute(
        text("DELETE FROM persons WHERE id = :pid RETURNING id"),
        {"pid": person_id},
    )
    deleted = res.first()
    cascade_summary["persons"] = 1 if deleted is not None else 0

    # (6) Best-effort external purge — never fail the legal purge on an
    # external-store hiccup. Each writer is lazy-imported + fully guarded.
    external = await _purge_external_best_effort(db, tenant_id, person_id)

    logger.info(
        "compliance.purge_person",
        tenant_id=tenant_id,
        person_id=person_id,
        cascade_summary=cascade_summary,
        ledger_anonymized=ledger_anonymized,
        external=external,
    )

    # Record the erasure itself as a fresh (un-redacted) ledger event.
    await _emit_erase_event(
        db,
        tenant_id=tenant_id,
        event_type="person.erase",
        aggregate_id=person_id,
        scope={
            "phase": "purge",
            "cascade_summary": cascade_summary,
            "ledger_anonymized": ledger_anonymized,
            "external": external,
        },
    )

    return {
        "person_id": pid_str,
        "cascade_summary": cascade_summary,
        "ledger_anonymized": ledger_anonymized,
        "external": external,
    }


# ---------------------------------------------------------------------------
# Tenant-level erasure
# ---------------------------------------------------------------------------


async def erase_tenant_account(db: AsyncSession, tenant_id: str) -> dict:
    """Erase an entire tenant account: purge every person + anonymize ledger.

    Iterates the tenant's persons (RLS-scoped) through :func:`purge_person`
    (which anonymizes each person's ledger events as it goes), then emits a
    tenant-scoped ``tenant.erase`` event documenting the sweep.

    Returns ``{"tenant_id", "persons_purged", "ledger_anonymized", "errors": [...]}``.
    """
    person_ids = [
        str(row[0])
        for row in (
            await db.execute(
                text("SELECT id FROM persons WHERE canonical_owner_tenant_id = :tid"),
                {"tid": tenant_id},
            )
        ).all()
    ]

    persons_purged = 0
    ledger_anonymized = 0
    errors: list[dict[str, str]] = []

    for pid in person_ids:
        try:
            summary = await purge_person(db, tenant_id, pid)
            persons_purged += 1
            ledger_anonymized += int(summary.get("ledger_anonymized", 0))
        except Exception as exc:  # noqa: BLE001 — one bad person must not abort the tenant sweep
            logger.warning(
                "compliance.erase_tenant.person_failed",
                tenant_id=tenant_id,
                person_id=pid,
                error=str(exc),
            )
            errors.append({"person_id": pid, "error": str(exc)})

    logger.info(
        "compliance.erase_tenant_account",
        tenant_id=tenant_id,
        persons_purged=persons_purged,
        ledger_anonymized=ledger_anonymized,
        errors=len(errors),
    )

    await _emit_erase_event(
        db,
        tenant_id=tenant_id,
        # aggregate_id points at the tenant for a tenant-scoped erasure.
        event_type="tenant.erase",
        aggregate_id=tenant_id,
        aggregate_type="tenant",
        scope={
            "phase": "tenant_erase",
            "persons_purged": persons_purged,
            "ledger_anonymized": ledger_anonymized,
            "errors": errors,
        },
    )

    return {
        "tenant_id": str(tenant_id),
        "persons_purged": persons_purged,
        "ledger_anonymized": ledger_anonymized,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Best-effort external purge (lazy-import + swallow)
# ---------------------------------------------------------------------------


async def _purge_external_best_effort(
    db: AsyncSession,
    tenant_id: str,
    person_id: str,
) -> dict:
    """Purge external data planes for a person; swallow every error to a warning.

    A missing or unhealthy external store (Qdrant / Garage / FalkorDB) must
    never fail the purge — the operational PII is already gone from Postgres,
    which is the legal source of truth. Each backend is lazy-imported so this
    module has no import-time dependency on them.
    """
    out: dict[str, Any] = {"qdrant": "skipped", "garage": "skipped", "falkordb": "skipped"}

    # --- Qdrant voice points -------------------------------------------------
    try:
        from contact_ops.services.qdrant_voice import get_voice_service

        deleted = await get_voice_service().delete_by_person_id(
            person_id=_as_uuid(person_id)
        )
        out["qdrant"] = {"deleted_points": int(deleted)}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "compliance.purge_external.qdrant_failed",
            tenant_id=tenant_id,
            person_id=person_id,
            error=str(exc),
        )
        out["qdrant"] = {"error": str(exc)}

    # --- Garage objects (per-tenant photo / voice buckets) -------------------
    try:
        from contact_ops.services.storage import get_storage_service

        slug = await db.scalar(
            text("SELECT slug FROM tenants WHERE id = :tid"), {"tid": tenant_id}
        )
        svc = get_storage_service()
        removed: list[str] = []
        if slug:
            # Photos are keyed by person id under the per-tenant photo bucket;
            # delete is itself best-effort + idempotent (404 is fine).
            bucket = svc.photo_bucket(str(slug))
            try:
                await svc.delete(bucket=bucket, key=f"{person_id}.jpg")
                removed.append(bucket)
            except Exception:  # noqa: BLE001 — per-object best effort
                pass
        out["garage"] = {"buckets_touched": removed}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "compliance.purge_external.garage_failed",
            tenant_id=tenant_id,
            person_id=person_id,
            error=str(exc),
        )
        out["garage"] = {"error": str(exc)}

    # --- FalkorDB graph node -------------------------------------------------
    try:
        from contact_ops.agents.graph_sync.falkordb_client import (
            FalkorDBGraphClient,
            TenantGraph,
            graph_name_for_slug,
        )

        slug = await db.scalar(
            text("SELECT slug FROM tenants WHERE id = :tid"), {"tid": tenant_id}
        )
        if slug:
            client = FalkorDBGraphClient()
            try:
                graph = TenantGraph(
                    tenant_id=_as_uuid(tenant_id),
                    graph_name=graph_name_for_slug(str(slug)),
                )
                await client.query(
                    graph,
                    "MATCH (p:Person {id: $pid}) DETACH DELETE p",
                    {"pid": str(person_id)},
                )
                out["falkordb"] = {"node_purged": True}
            finally:
                await client.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "compliance.purge_external.falkordb_failed",
            tenant_id=tenant_id,
            person_id=person_id,
            error=str(exc),
        )
        out["falkordb"] = {"error": str(exc)}

    return out


# ---------------------------------------------------------------------------
# Ledger emit for the erasure event itself
# ---------------------------------------------------------------------------


async def _emit_erase_event(
    db: AsyncSession,
    *,
    tenant_id: str,
    event_type: str,
    aggregate_id: str,
    scope: dict[str, Any],
    aggregate_type: str = "person",
) -> None:
    """Append a hash-chained ``action_event`` documenting the erasure scope.

    Written via the same RLS-bound ``db`` (not the MCP audit path) because
    these service helpers take a bare session. Computes ``content_hash`` over
    the canonical payload and links ``prev_event_hash`` to the tenant's latest
    event, exactly like ``audit_helper.emit_action_event``. The scope here is
    NON-PII (counts + reason), so it is never itself subject to redaction.
    """
    payload = {"before": None, "after": scope}
    payload_bytes = json.dumps(
        payload, sort_keys=True, default=str, separators=(",", ":")
    ).encode()

    prev_hash = await db.scalar(
        text(
            """
            SELECT content_hash FROM action_event
            WHERE tenant_id = :tid
            ORDER BY proposed_at DESC
            LIMIT 1
            """
        ),
        {"tid": tenant_id},
    )

    await db.execute(
        text(
            """
            INSERT INTO action_event (
                event_type, tenant_id, aggregate_type, aggregate_id,
                affected_ids, payload, actor, actor_type, evidence,
                status, content_hash, prev_event_hash
            ) VALUES (
                :event_type, :tid, CAST(:aggregate_type AS entity_kind), :aggregate_id,
                '{}'::uuid[], CAST(:payload AS jsonb),
                CAST(:actor AS jsonb), CAST('external_system' AS actor_type),
                CAST(:evidence AS jsonb),
                CAST('applied' AS event_status), :content_hash, :prev_hash
            )
            """
        ),
        {
            "event_type": event_type,
            "tid": tenant_id,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "payload": json.dumps(payload, default=str),
            "actor": json.dumps({"sub": "service:compliance-erasure"}),
            "evidence": json.dumps({"source": "compliance_erasure"}),
            "content_hash": hashlib.sha256(payload_bytes).digest(),
            "prev_hash": prev_hash,
        },
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _as_uuid(value: str | uuid_module.UUID) -> uuid_module.UUID:
    if isinstance(value, uuid_module.UUID):
        return value
    return uuid_module.UUID(str(value))


__all__ = [
    "tombstone_person",
    "purge_person",
    "anonymize_subject_action_events",
    "erase_tenant_account",
]
