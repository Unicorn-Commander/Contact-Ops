"""Synchronous propose-only vCard import orchestration.

Parsing lives here; the per-record dedup + Review-Queue emit (shared with the
CSV importer) lives in :mod:`contact_ops.services.import_propose`. That shared
loop also gives vCard imports existing-person duplicate detection, so a
re-import of contacts you already have is collapsed instead of re-proposed.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING

import structlog

from contact_ops.importers.vcard import record_from_vcard_text, split_vcards
from contact_ops.services.import_propose import (
    ImportPreview,
    ImportRowError,
    ImportSummary,
    propose_canonical_records,
)
from contact_ops.services.proposal_emit import (
    PERSON_CREATE_PROPOSAL_PATH,
    upload_id_for_text,
)

if TYPE_CHECKING:
    # Type-only (see import_propose.py): a runtime MCPContext import here can
    # re-enter the eager MCP tool registry mid-initialization.
    from contact_ops.mcp.registry import MCPContext

MAX_VCARD_SIZE_BYTES = 50 * 1024 * 1024
VCARD_IMPORT_PROPOSAL_TOOL = PERSON_CREATE_PROPOSAL_PATH

logger = structlog.get_logger(__name__)

# Back-compat aliases — the REST endpoint and MCP tool import these names.
VCardImportPreview = ImportPreview
VCardImportRowError = ImportRowError
VCardImportResult = ImportSummary


class VCardImportError(ValueError):
    """Raised for invalid vCard uploads."""


async def propose_vcard_import(
    *,
    ctx: MCPContext,
    vcard_text: str,
    dry_run: bool = False,
    auto_approve: bool = False,
    filename: str | None = None,
    upload_id: uuid.UUID | None = None,
) -> ImportSummary:
    """Parse vCards and propose person-create events without applying them."""

    if not _looks_like_vcard(vcard_text):
        raise VCardImportError("file content must start with BEGIN:VCARD")

    resolved_upload_id = upload_id or upload_id_for_text("vcard-import", vcard_text)
    source_filename = _sanitize_filename(filename)
    blocks = split_vcards(vcard_text)
    if not blocks:
        raise VCardImportError("no complete vCard records found")

    records = []
    parse_errors: list[ImportRowError] = []
    for index, block in enumerate(blocks):
        try:
            record = record_from_vcard_text(
                block, source_record_id=_source_record_id(block, index)
            )
        except Exception as exc:  # noqa: BLE001 - report the block, keep going
            parse_errors.append(ImportRowError(index=index, reason=str(exc)))
            continue
        records.append(record)

    result = await propose_canonical_records(
        ctx=ctx,
        records=records,
        source_kind="vcard",
        source_action="vcard_import",
        upload_id=resolved_upload_id,
        filename=source_filename,
        dry_run=dry_run,
        auto_approve=auto_approve,
        parse_errors=parse_errors,
    )
    logger.info(
        "vcard_import_completed",
        tenant_id=str(ctx.tenant_id),
        upload_id=str(resolved_upload_id),
        parsed_count=result.parsed_count,
        proposed_count=result.proposed_count,
        duplicate_count=result.duplicate_count,
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
    return text_value.lstrip("﻿\r\n\t ").upper().startswith("BEGIN:VCARD")


def _source_record_id(block: str, index: int) -> str:
    digest = hashlib.sha256(block.encode()).hexdigest()
    return f"vcard:{index}:{digest}"


def _sanitize_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    cleaned = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    return cleaned[:255] or None
