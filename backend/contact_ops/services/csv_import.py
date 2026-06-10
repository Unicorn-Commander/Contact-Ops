"""Propose-only CSV contact import (Google Contacts / LinkedIn exports).

Mirrors :mod:`contact_ops.services.vcard_import` but for the CSV exports people
actually download. Google Contacts defaults to CSV (not vCard), so this closes
the gap where the GUI only accepted ``.vcf``. Per-row parsing means one bad row
is reported, not fatal; everything then funnels through the shared propose loop
(in-file dedup + existing-person detection + Review-Queue emit).
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import TYPE_CHECKING

import structlog

from contact_ops.importers import google_csv, linkedin_csv
from contact_ops.importers.base import CanonicalImportRecord, SourceKind
from contact_ops.services.import_propose import (
    ImportRowError,
    ImportSummary,
    propose_canonical_records,
)
from contact_ops.services.proposal_emit import upload_id_for_text

if TYPE_CHECKING:
    # See import_propose.py — runtime import of MCPContext causes a circular
    # import via the eager MCP tool registry. Type-only is enough here.
    from contact_ops.mcp.registry import MCPContext

MAX_CSV_SIZE_BYTES = 50 * 1024 * 1024

logger = structlog.get_logger(__name__)


class CSVImportError(ValueError):
    """Raised for invalid or unrecognized CSV uploads."""


# Header markers distinctive enough to tell the supported exports apart.
_LINKEDIN_MARKERS = {"connected on", "email address", "position"}


def decode_csv_bytes(data: bytes) -> str:
    if len(data) > MAX_CSV_SIZE_BYTES:
        raise CSVImportError("CSV file exceeds 50 MiB")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CSVImportError("CSV file must be valid UTF-8 text") from exc


def detect_csv_source_kind(header: list[str]) -> SourceKind | None:
    """Pick the importer from the CSV header row, or None if unrecognized."""

    fields = {(h or "").strip().lower() for h in header}
    # LinkedIn Connections.csv has a "Connected On" column nothing else uses.
    if "connected on" in fields or _LINKEDIN_MARKERS <= fields:
        return "linkedin_csv"
    # Google Contacts: "Labels", numbered "E-mail N - Value", "Organization Name".
    if (
        "labels" in fields
        or "organization name" in fields
        or any(f.startswith("e-mail 1 - value") for f in fields)
        or "given name" in fields
    ):
        return "google_csv"
    return None


def _parse_rows(
    *, csv_text: str, source_kind: SourceKind
) -> tuple[list[CanonicalImportRecord], list[ImportRowError]]:
    """Parse each non-empty CSV row, collecting per-row errors instead of failing."""

    records: list[CanonicalImportRecord] = []
    errors: list[ImportRowError] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for index, row in enumerate(reader, start=2):  # row 1 is the header
        if not any((value or "").strip() for value in row.values()):
            continue
        try:
            if source_kind == "linkedin_csv":
                record = linkedin_csv.record_from_row(row, row_number=index)
            else:
                record = google_csv.record_from_row(
                    row, row_number=index, default_country="US"
                )
        except Exception as exc:  # noqa: BLE001 - report the row, keep going
            errors.append(ImportRowError(index=index, reason=str(exc)))
            continue
        records.append(record)
    return records, errors


async def propose_csv_import(
    *,
    ctx: MCPContext,
    csv_text: str,
    dry_run: bool = False,
    auto_approve: bool = False,
    filename: str | None = None,
    upload_id: uuid.UUID | None = None,
) -> ImportSummary:
    """Parse a contacts CSV and propose person-create events without applying them."""

    header_reader = csv.reader(io.StringIO(csv_text))
    header = next(header_reader, [])
    if not header:
        raise CSVImportError("CSV file is empty")

    source_kind = detect_csv_source_kind(header)
    if source_kind is None:
        raise CSVImportError(
            "Unrecognized CSV format. Supported: Google Contacts CSV and "
            "LinkedIn Connections.csv. For other sources (Outlook, iCloud, …), "
            "export as vCard (.vcf) and use the vCard import."
        )

    records, parse_errors = _parse_rows(csv_text=csv_text, source_kind=source_kind)
    if not records and not parse_errors:
        raise CSVImportError("no contact rows found in CSV")

    resolved_upload_id = upload_id or upload_id_for_text(
        f"csv-import:{source_kind}", csv_text
    )
    summary = await propose_canonical_records(
        ctx=ctx,
        records=records,
        source_kind=source_kind,
        source_action="csv_import",
        upload_id=resolved_upload_id,
        filename=_sanitize_filename(filename),
        dry_run=dry_run,
        auto_approve=auto_approve,
        parse_errors=parse_errors,
    )
    logger.info(
        "csv_import_completed",
        tenant_id=str(ctx.tenant_id),
        source_kind=source_kind,
        upload_id=str(resolved_upload_id),
        parsed_count=summary.parsed_count,
        proposed_count=summary.proposed_count,
        duplicate_count=summary.duplicate_count,
    )
    return summary


def _sanitize_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    cleaned = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    return cleaned[:255] or None
