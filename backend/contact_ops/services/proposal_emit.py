"""Shared propose-only person-create emitter for imports and connectors."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import text

from contact_ops.importers.base import CanonicalImportRecord

if TYPE_CHECKING:
    from contact_ops.mcp.registry import MCPContext

PERSON_CREATE_PROPOSAL_PATH = "action_event:person.create:proposed"


async def emit_person_create_proposal(
    *,
    ctx: MCPContext,
    record: CanonicalImportRecord,
    upload_id: uuid.UUID,
    source: dict[str, object],
    confidence: float = 0.9,
) -> uuid.UUID:
    """Emit a Review Queue proposal without inserting a Person row.

    ``confidence`` defaults to 0.9 (below the confidence-approver's 0.95 bar, so
    the proposal waits for human review). An owner-trusted import can pass a
    value >=0.95 so the post-import pipeline auto-approves it."""

    idempotency_key = _idempotency_key(ctx.tenant_id, upload_id, record)
    existing = await ctx.audit_db.scalar(
        text(
            """
            SELECT event_id
            FROM action_event
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND idempotency_key = :idempotency_key
            LIMIT 1
            """
        ),
        {"tenant_id": str(ctx.tenant_id), "idempotency_key": idempotency_key},
    )
    if isinstance(existing, uuid.UUID):
        return existing
    if existing is not None:
        return uuid.UUID(str(existing))

    proposed_person_id = uuid.uuid5(upload_id, record.source_record_id)
    payload_after = record_to_person_payload(
        record=record,
        person_id=proposed_person_id,
        source=source,
    )
    from contact_ops.mcp.audit_helper import emit_action_event

    event_id = await emit_action_event(
        ctx,
        event_type="person.create",
        aggregate_type="person",
        aggregate_id=proposed_person_id,
        payload_before=None,
        payload_after=payload_after,
        status="proposed",
        confidence=confidence,
        rationale="Proposed person creation from contact import. No contact row was applied.",
        evidence=dict(source),
    )
    await ctx.audit_db.execute(
        text(
            """
            UPDATE action_event
            SET idempotency_key = :idempotency_key,
                decision_payload = CAST(:decision_payload AS jsonb),
                reversibility_class = 'reversible',
                triggered_by = :triggered_by
            WHERE event_id = CAST(:event_id AS uuid)
            """
        ),
        {
            "event_id": str(event_id),
            "idempotency_key": idempotency_key,
            "triggered_by": str(source.get("action", "contact_import")),
            "decision_payload": json.dumps(
                {"payload_before": None, "payload_after": payload_after},
                sort_keys=True,
                default=str,
            ),
        },
    )
    return event_id


def record_to_person_payload(
    *,
    record: CanonicalImportRecord,
    person_id: uuid.UUID,
    source: dict[str, object],
) -> dict[str, object]:
    return {
        "person_id": str(person_id),
        "display_name": record.display_name,
        "given_name": record.given_name,
        "family_name": record.family_name,
        "additional_names": record.additional_names,
        "honorific_prefix": record.honorific_prefix,
        "honorific_suffix": record.honorific_suffix,
        "nicknames": record.nicknames,
        "birthday": record.birthday,
        "notes": record.notes,
        "headline": record.headline,
        "occupation_title": record.occupation_title,
        "emails": [email.model_dump(mode="json") for email in record.emails],
        "phones": [phone.model_dump(mode="json") for phone in record.phones],
        "addresses": [address.model_dump(mode="json") for address in record.addresses],
        "identifiers": [identifier.model_dump(mode="json") for identifier in record.identifiers],
        "employments": [employment.model_dump(mode="json") for employment in record.employments],
        "tags": record.tags,
        "source": source,
    }


def upload_id_for_text(namespace: str, value: str) -> uuid.UUID:
    digest = hashlib.sha256(value.encode()).hexdigest()
    return uuid.uuid5(uuid.NAMESPACE_URL, f"contact-ops:{namespace}:{digest}")


def import_fingerprint(record: CanonicalImportRecord) -> str:
    emails = sorted(email.address.strip().lower() for email in record.emails if email.address)
    phones = sorted(_normalize_phone(phone.e164) for phone in record.phones if phone.e164)
    payload = {
        "display_name": record.display_name.strip().lower(),
        "emails": emails,
        "phones": phones,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _idempotency_key(
    tenant_id: uuid.UUID,
    upload_id: uuid.UUID,
    record: CanonicalImportRecord,
) -> str:
    payload = {
        "tenant_id": str(tenant_id),
        "upload_id": str(upload_id),
        "source_record_id": record.source_record_id,
        "fingerprint": import_fingerprint(record),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalize_phone(value: str) -> str:
    if value.startswith("+"):
        return "+" + re.sub(r"\D", "", value)
    return re.sub(r"\D", "", value)
