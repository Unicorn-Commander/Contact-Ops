"""Synchronous propose-only vCard import orchestration."""

from __future__ import annotations

import hashlib
import json
import re
import uuid

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import text

from contact_ops.importers.base import CanonicalImportRecord
from contact_ops.importers.vcard import record_from_vcard_text, split_vcards
from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.registry import MCPContext

MAX_VCARD_SIZE_BYTES = 50 * 1024 * 1024
VCARD_IMPORT_PROPOSAL_TOOL = "action_event:person.create:proposed"

logger = structlog.get_logger(__name__)


class VCardImportError(ValueError):
    """Raised for invalid vCard uploads."""


class VCardImportPreview(BaseModel):
    display_name: str
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    organization: str | None = None


class VCardImportRowError(BaseModel):
    index: int
    reason: str


class VCardImportResult(BaseModel):
    parsed_count: int
    proposed_count: int
    deduped_count: int
    skipped_count: int
    errors: list[VCardImportRowError]
    preview: list[VCardImportPreview]


async def propose_vcard_import(
    *,
    ctx: MCPContext,
    vcard_text: str,
    dry_run: bool = False,
    filename: str | None = None,
    upload_id: uuid.UUID | None = None,
) -> VCardImportResult:
    """Parse vCards and propose person-create events without applying them."""

    if not _looks_like_vcard(vcard_text):
        raise VCardImportError("file content must start with BEGIN:VCARD")

    resolved_upload_id = upload_id or _upload_id_for_text(vcard_text)
    source_filename = _sanitize_filename(filename)
    blocks = split_vcards(vcard_text)
    if not blocks:
        raise VCardImportError("no complete vCard records found")

    parsed_count = 0
    proposed_count = 0
    deduped_count = 0
    errors: list[VCardImportRowError] = []
    preview: list[VCardImportPreview] = []
    seen: set[str] = set()

    for index, block in enumerate(blocks):
        try:
            record = record_from_vcard_text(block, source_record_id=_source_record_id(block, index))
        except Exception as exc:
            errors.append(VCardImportRowError(index=index, reason=str(exc)))
            continue

        parsed_count += 1
        if len(preview) < 5:
            preview.append(_preview(record))

        fingerprint = _fingerprint(record)
        if fingerprint in seen:
            deduped_count += 1
            continue
        seen.add(fingerprint)

        if dry_run:
            continue

        await _emit_person_create_proposal(
            ctx=ctx,
            record=record,
            upload_id=resolved_upload_id,
            filename=source_filename,
            index=index,
        )
        proposed_count += 1

    skipped_count = len(errors)
    result = VCardImportResult(
        parsed_count=parsed_count,
        proposed_count=proposed_count,
        deduped_count=deduped_count,
        skipped_count=skipped_count,
        errors=errors,
        preview=preview,
    )
    logger.info(
        "vcard_import_completed",
        tenant_id=str(ctx.tenant_id),
        upload_id=str(resolved_upload_id),
        parsed_count=result.parsed_count,
        proposed_count=result.proposed_count,
    )
    return result


def decode_vcard_bytes(data: bytes) -> str:
    if len(data) > MAX_VCARD_SIZE_BYTES:
        raise VCardImportError("vCard file exceeds 50 MiB")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise VCardImportError("vCard file must be valid UTF-8 text") from exc


def _looks_like_vcard(text_value: str) -> bool:
    return text_value.lstrip("\ufeff\r\n\t ").upper().startswith("BEGIN:VCARD")


def _source_record_id(block: str, index: int) -> str:
    digest = hashlib.sha256(block.encode()).hexdigest()
    return f"vcard:{index}:{digest}"


def _upload_id_for_text(vcard_text: str) -> uuid.UUID:
    digest = hashlib.sha256(vcard_text.encode()).hexdigest()
    return uuid.uuid5(uuid.NAMESPACE_URL, f"contact-ops:vcard-import:{digest}")


def _fingerprint(record: CanonicalImportRecord) -> str:
    emails = sorted(email.address.strip().lower() for email in record.emails if email.address)
    phones = sorted(_normalize_phone(phone.e164) for phone in record.phones if phone.e164)
    payload = {
        "display_name": record.display_name.strip().lower(),
        "emails": emails,
        "phones": phones,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _normalize_phone(value: str) -> str:
    if value.startswith("+"):
        return "+" + re.sub(r"\D", "", value)
    return re.sub(r"\D", "", value)


def _preview(record: CanonicalImportRecord) -> VCardImportPreview:
    organization = record.employments[0].company if record.employments else None
    return VCardImportPreview(
        display_name=record.display_name,
        emails=[email.address for email in record.emails],
        phones=[phone.e164 for phone in record.phones],
        organization=organization,
    )


async def _emit_person_create_proposal(
    *,
    ctx: MCPContext,
    record: CanonicalImportRecord,
    upload_id: uuid.UUID,
    filename: str | None,
    index: int,
) -> uuid.UUID:
    idempotency_key = _idempotency_key(ctx.tenant_id, upload_id, record)
    existing = await ctx.audit_db.scalar(
        text(
            """
            SELECT event_id
            FROM action_event
            WHERE tenant_id = :tenant_id
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
    source = {
        "action": "vcard_import",
        "upload_id": str(upload_id),
        "filename": filename,
        "record_index": index,
        "source_record_id": record.source_record_id,
    }
    payload_after = {
        "person_id": str(proposed_person_id),
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
    event_id = await emit_action_event(
        ctx,
        event_type="person.create",
        aggregate_type="person",
        aggregate_id=proposed_person_id,
        payload_before=None,
        payload_after=payload_after,
        status="proposed",
        confidence=0.9,
        rationale="Proposed person creation from uploaded vCard. No contact row was applied.",
        evidence=source,
    )
    await ctx.audit_db.execute(
        text(
            """
            UPDATE action_event
            SET idempotency_key = :idempotency_key,
                decision_payload = CAST(:decision_payload AS jsonb),
                reversibility_class = 'reversible',
                triggered_by = 'vcard_import'
            WHERE event_id = :event_id
            """
        ),
        {
            "event_id": str(event_id),
            "idempotency_key": idempotency_key,
            "decision_payload": json.dumps(
                {"payload_before": None, "payload_after": payload_after},
                sort_keys=True,
                default=str,
            ),
        },
    )
    return event_id


def _idempotency_key(
    tenant_id: uuid.UUID,
    upload_id: uuid.UUID,
    record: CanonicalImportRecord,
) -> str:
    payload = {
        "tenant_id": str(tenant_id),
        "upload_id": str(upload_id),
        "source_record_id": record.source_record_id,
        "fingerprint": _fingerprint(record),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sanitize_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    cleaned = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    return cleaned[:255] or None
