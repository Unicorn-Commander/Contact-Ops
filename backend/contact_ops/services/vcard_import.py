"""Synchronous propose-only vCard import orchestration."""

from __future__ import annotations

import hashlib
import uuid

import structlog
from pydantic import BaseModel, Field

from contact_ops.importers.base import CanonicalImportRecord
from contact_ops.importers.vcard import record_from_vcard_text, split_vcards
from contact_ops.mcp.registry import MCPContext
from contact_ops.services.proposal_emit import (
    PERSON_CREATE_PROPOSAL_PATH,
    emit_person_create_proposal,
    import_fingerprint,
    upload_id_for_text,
)

MAX_VCARD_SIZE_BYTES = 50 * 1024 * 1024
VCARD_IMPORT_PROPOSAL_TOOL = PERSON_CREATE_PROPOSAL_PATH

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

    resolved_upload_id = upload_id or upload_id_for_text("vcard-import", vcard_text)
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

        fingerprint = import_fingerprint(record)
        if fingerprint in seen:
            deduped_count += 1
            continue
        seen.add(fingerprint)

        if dry_run:
            continue

        await emit_person_create_proposal(
            ctx=ctx,
            record=record,
            upload_id=resolved_upload_id,
            source={
                "action": "vcard_import",
                "upload_id": str(resolved_upload_id),
                "filename": source_filename,
                "record_index": index,
                "source_record_id": record.source_record_id,
            },
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


def _preview(record: CanonicalImportRecord) -> VCardImportPreview:
    organization = record.employments[0].company if record.employments else None
    return VCardImportPreview(
        display_name=record.display_name,
        emails=[email.address for email in record.emails],
        phones=[phone.e164 for phone in record.phones],
        organization=organization,
    )


def _sanitize_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    cleaned = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    return cleaned[:255] or None
