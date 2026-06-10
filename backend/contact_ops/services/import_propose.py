"""Shared propose-only loop for file imports (vCard + CSV).

Both the vCard and CSV importers turn a file into a list of
:class:`CanonicalImportRecord` and then funnel through here, so they behave
identically: in-file dedup, Review-Queue emit, and — the part that makes a
*re-import* safe — existing-person duplicate detection.

A record whose email or phone already belongs to a person in this tenant is
collapsed (counted as ``duplicate_count``) instead of being re-proposed, so
re-importing the contacts you already have doesn't flood the Review Queue with
hundreds of "new" proposals for people you already track.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from sqlalchemy import text

from contact_ops.importers.base import CanonicalImportRecord, SourceKind
from contact_ops.services.proposal_emit import (
    emit_person_create_proposal,
    import_fingerprint,
)

if TYPE_CHECKING:
    # Import-time only: pulling MCPContext at runtime eagerly triggers the MCP
    # tool registry (register_all_tools), which re-imports this module before it
    # finishes initializing — a circular import. proposal_emit.py guards it the
    # same way. Annotations are strings (`from __future__`), so this is enough.
    from contact_ops.mcp.registry import MCPContext

_PREVIEW_LIMIT = 8


class ImportPreview(BaseModel):
    display_name: str
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    organization: str | None = None
    duplicate: bool = False  # already exists in this tenant's contacts


class ImportRowError(BaseModel):
    index: int
    reason: str


class ImportSummary(BaseModel):
    parsed_count: int
    proposed_count: int
    deduped_count: int  # collapsed duplicates WITHIN this file
    duplicate_count: int = 0  # matched a person already in your contacts
    skipped_count: int
    errors: list[ImportRowError] = Field(default_factory=list)
    preview: list[ImportPreview] = Field(default_factory=list)


def _normalize_phone(value: str) -> str:
    # Mirror proposal_emit._normalize_phone so the index keys line up.
    if value.startswith("+"):
        return "+" + re.sub(r"\D", "", value)
    return re.sub(r"\D", "", value)


async def _load_existing_index(
    ctx: MCPContext,
) -> tuple[dict[str, uuid.UUID], dict[str, uuid.UUID]]:
    """Build email(lower)->person_id and phone(normalized)->person_id maps.

    Tenant-scoped explicitly via ``current_tenant_id()`` — the same GUC the RLS
    policies read, already bound on ``ctx.db`` by the caller. We join through
    ``persons`` (which is itself RLS-fenced) and add the explicit predicate as
    belt-and-suspenders so a cross-tenant email can never leak into the index.
    Loaded once per import (cheap: a few hundred rows) instead of one query per
    record.
    """

    email_index: dict[str, uuid.UUID] = {}
    phone_index: dict[str, uuid.UUID] = {}

    email_rows = await ctx.db.execute(
        text(
            """
            SELECT lower(e.address) AS k, e.person_id AS pid
            FROM emails e
            JOIN persons p ON p.id = e.person_id
            WHERE e.person_id IS NOT NULL
              AND p.canonical_owner_tenant_id = current_tenant_id()
            """
        )
    )
    for row in email_rows:
        if row.k and row.pid is not None:
            email_index.setdefault(str(row.k), row.pid)

    phone_rows = await ctx.db.execute(
        text(
            """
            SELECT ph.e164 AS k, ph.person_id AS pid
            FROM phones ph
            JOIN persons p ON p.id = ph.person_id
            WHERE ph.person_id IS NOT NULL
              AND p.canonical_owner_tenant_id = current_tenant_id()
            """
        )
    )
    for row in phone_rows:
        if row.k and row.pid is not None:
            phone_index.setdefault(_normalize_phone(str(row.k)), row.pid)

    return email_index, phone_index


def _match_existing(
    record: CanonicalImportRecord,
    email_index: dict[str, uuid.UUID],
    phone_index: dict[str, uuid.UUID],
) -> uuid.UUID | None:
    for email in record.emails:
        if email.address:
            pid = email_index.get(email.address.strip().lower())
            if pid is not None:
                return pid
    for phone in record.phones:
        if phone.e164:
            pid = phone_index.get(_normalize_phone(phone.e164))
            if pid is not None:
                return pid
    return None


def _preview(record: CanonicalImportRecord, *, duplicate: bool) -> ImportPreview:
    organization = record.employments[0].company if record.employments else None
    return ImportPreview(
        display_name=record.display_name,
        emails=[email.address for email in record.emails],
        phones=[phone.e164 for phone in record.phones],
        organization=organization,
        duplicate=duplicate,
    )


async def propose_canonical_records(
    *,
    ctx: MCPContext,
    records: list[CanonicalImportRecord],
    source_kind: SourceKind | str,
    source_action: str,
    upload_id: uuid.UUID,
    filename: str | None,
    dry_run: bool = False,
    auto_approve: bool = False,
    parse_errors: list[ImportRowError] | None = None,
) -> ImportSummary:
    """Dedup, detect existing people, and emit Review-Queue proposals.

    ``parse_errors`` lets the caller pass rows that failed to parse (vCard does
    this per-block) so they surface in the summary's ``errors``/``skipped_count``.
    """

    errors = list(parse_errors or [])
    email_index, phone_index = await _load_existing_index(ctx)

    parsed_count = 0
    proposed_count = 0
    deduped_count = 0
    duplicate_count = 0
    preview: list[ImportPreview] = []
    seen: set[str] = set()
    emitted_ids: list[uuid.UUID] = []

    for index, record in enumerate(records):
        parsed_count += 1
        existing = _match_existing(record, email_index, phone_index)
        if len(preview) < _PREVIEW_LIMIT:
            preview.append(_preview(record, duplicate=existing is not None))

        fingerprint = import_fingerprint(record)
        if fingerprint in seen:
            deduped_count += 1
            continue
        seen.add(fingerprint)

        if existing is not None:
            # Already in this tenant's contacts — count it, don't re-propose.
            duplicate_count += 1
            continue

        if dry_run:
            continue

        eid = await emit_person_create_proposal(
            ctx=ctx,
            record=record,
            upload_id=upload_id,
            source={
                "action": source_action,
                "source_kind": str(source_kind),
                "upload_id": str(upload_id),
                "filename": filename,
                "record_index": index,
                "source_record_id": record.source_record_id,
            },
        )
        emitted_ids.append(eid)
        proposed_count += 1

    if not dry_run and proposed_count > 0:
        # P0: auto-process freshly-imported proposals so CSV/vCard imports don't
        # sit in the Review Queue forever. The connector post-pull path already
        # runs this pipeline; the import path did not, so imported contacts were
        # stuck unapproved (and, pre-fix, never reached the graph). Same session
        # as the emits above — committed together by the caller (framework). The
        # agents flush, never self-commit, so the GUC stays bound. dedup -> quality
        # -> confidence-approve: high-confidence reversible proposals auto-apply
        # (and now sync to the graph via the approval path); the rest wait for a
        # human. Lazy import to avoid the tools-package circular-import landmine.
        from contact_ops.services.agents import (
            run_confidence_approver,
            run_dedup_agent,
            run_quality_filter,
        )

        await run_dedup_agent(ctx.db, tenant_id=ctx.tenant_id)
        await run_quality_filter(ctx.db, tenant_id=ctx.tenant_id)
        await run_confidence_approver(ctx.db, tenant_id=ctx.tenant_id)

        if auto_approve and emitted_ids:
            # Owner chose to TRUST this import → directly effect the proposals
            # that survived dedup/quality. We can't lean on the confidence-
            # approver here: emit_action_event NULLs confidence for human actors
            # (and imports are human-triggered), so a human import never clears
            # the >=0.95 bar. Apply each surviving 'proposed' row (create person +
            # children + graph node) and flip it to applied — reusing the approval
            # applier with one shared FalkorDB client for the batch.
            from contact_ops.agents.graph_sync.falkordb_client import FalkorDBGraphClient
            from contact_ops.services.inbox_mutations import _apply_person_create_proposal

            graph_client = FalkorDBGraphClient()
            try:
                for eid in emitted_ids:
                    status_now = await ctx.db.scalar(
                        text("SELECT status FROM action_event WHERE event_id = CAST(:e AS uuid)"),
                        {"e": str(eid)},
                    )
                    if status_now != "proposed":
                        continue  # archived by quality, merged by dedup, etc.
                    await _apply_person_create_proposal(
                        db=ctx.db, tenant_id=ctx.tenant_id, proposal_id=eid, graph_client=graph_client
                    )
                    await ctx.db.execute(
                        text(
                            "UPDATE action_event SET status='applied', applied_at=now() "
                            "WHERE event_id = CAST(:e AS uuid)"
                        ),
                        {"e": str(eid)},
                    )
            finally:
                await graph_client.close()

    return ImportSummary(
        parsed_count=parsed_count,
        proposed_count=proposed_count,
        deduped_count=deduped_count,
        duplicate_count=duplicate_count,
        skipped_count=len(errors),
        errors=errors,
        preview=preview,
    )
