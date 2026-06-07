"""Importer writer that routes every mutation through MCP tools."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import select

from contact_ops.core.config import get_settings
from contact_ops.core.database import bind_session_context
from contact_ops.importers.base import CanonicalImportRecord, ImportStats, ProvenanceContext
from contact_ops.mcp.errors import AMBIGUOUS_MATCH, DUPLICATE_RECORD, ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.addresses import AddAddressInput, add_address
from contact_ops.mcp.tools.emails import AddEmailInput, add_email
from contact_ops.mcp.tools.employment import SetEmploymentInput, set_employment
from contact_ops.mcp.tools.identifiers import AddIdentifierInput, add_identifier
from contact_ops.mcp.tools.orgs import CreateOrganizationInput, create_organization
from contact_ops.mcp.tools.people import (
    Birthday,
    CreatePersonInput,
    FindPersonByIdentifierInput,
    IdentifierInput,
    UpsertPersonInput,
    create_person,
    find_person_by_identifier,
    upsert_person,
)
from contact_ops.mcp.tools.phones import AddPhoneInput, add_phone
from contact_ops.mcp.tools.tags import TagPersonInput, tag_person
from contact_ops.models import Organization

metadata = sa.MetaData()

sources_table = sa.Table(
    "sources",
    metadata,
    sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
    # DB column is the `source_type` Postgres enum, not text — typing it as Text
    # made SQLAlchemy emit `= $N::VARCHAR`, which Postgres rejects (no enum=varchar
    # operator), aborting every import row. Reference the existing enum (no recreate).
    sa.Column(
        "source_type",
        sa.dialects.postgresql.ENUM(name="source_type", create_type=False),
    ),
    sa.Column("source_uri", sa.Text),
    sa.Column("source_record_id", sa.Text),
    sa.Column("retrieval_method", sa.Text),
    sa.Column("source_reliability_base", sa.Numeric(4, 3)),
    sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True)),
)
facts_table = sa.Table(
    "facts",
    metadata,
    sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True)),
    sa.Column("subject_kind", sa.Text),
    sa.Column("subject_id", sa.dialects.postgresql.UUID(as_uuid=True)),
    sa.Column("predicate", sa.Text),
    sa.Column("object_kind", sa.Text),
    sa.Column("object_value", sa.dialects.postgresql.JSONB),
    sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True)),
    sa.Column("confidence", sa.Numeric(4, 3)),
    sa.Column("observed_at", sa.DateTime(timezone=True)),
)
field_provenance_table = sa.Table(
    "field_provenance",
    metadata,
    sa.Column("entity_type", sa.Text),
    sa.Column("entity_id", sa.dialects.postgresql.UUID(as_uuid=True)),
    sa.Column("field_path", sa.Text),
    sa.Column("current_value", sa.dialects.postgresql.JSONB),
    sa.Column("set_by_event_id", sa.dialects.postgresql.UUID(as_uuid=True)),
    sa.Column("set_by_actor", sa.dialects.postgresql.JSONB),
    # Also the `source_type` enum in the DB (see sources_table note above).
    sa.Column(
        "source",
        sa.dialects.postgresql.ENUM(name="source_type", create_type=False),
    ),
    sa.Column("source_record_id", sa.Text),
    sa.Column("confidence", sa.Numeric(4, 3)),
    sa.Column("established_at", sa.DateTime(timezone=True)),
    sa.Column("history", sa.dialects.postgresql.JSONB),
)
merge_candidates_table = sa.Table(
    "merge_candidates",
    metadata,
    sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True)),
    sa.Column("source_person_id", sa.dialects.postgresql.UUID(as_uuid=True)),
    sa.Column("candidate_person_ids", sa.dialects.postgresql.JSONB),
    sa.Column("source_type", sa.Text),
    sa.Column("source_record_id", sa.Text),
    sa.Column("confidence", sa.Numeric(4, 3)),
    sa.Column("reason", sa.Text),
)


@dataclass(slots=True)
class DryRunItem:
    action: Literal["create", "upsert", "ambiguous"]
    display_name: str
    identifiers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WriteResult:
    stats: ImportStats = field(default_factory=ImportStats)
    dry_run_items: list[DryRunItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class MCPImporterWriter:
    def __init__(
        self,
        *,
        ctx: MCPContext,
        provenance: ProvenanceContext,
        dry_run: bool = True,
        dedup_threshold: float = 0.85,
    ) -> None:
        self.ctx = ctx
        self.provenance = provenance
        self.dry_run = dry_run
        self.dedup_threshold = dedup_threshold

    async def write_records(self, records: list[CanonicalImportRecord]) -> WriteResult:
        result = WriteResult()
        settings = get_settings()
        uc_uid = str(self.ctx.claims.get("uc_uid") or self.ctx.user_id)

        async def _rebind() -> None:
            # Per-record commits end the transaction, which clears the
            # transaction-scoped `SET LOCAL app.tenant_id` GUC that RLS keys off.
            # Re-establish it before each record's writes (and after the loop, so
            # the caller's post-write work still has tenant context).
            await bind_session_context(self.ctx.db, str(self.ctx.tenant_id), uc_uid, settings)
            await bind_session_context(self.ctx.audit_db, str(self.ctx.tenant_id), uc_uid, settings)

        for record in records:
            try:
                if not self.dry_run:
                    await _rebind()
                await self._write_one(record, result)
                if not self.dry_run:
                    # Commit per record so one bad row can't abort the whole batch's
                    # shared (db + audit) transaction and roll back the good rows.
                    await self.ctx.db.commit()
                    await self.ctx.audit_db.commit()
            except Exception as exc:  # noqa: BLE001 - import should continue and report row-level failures.
                if not self.dry_run:
                    await self.ctx.db.rollback()
                    await self.ctx.audit_db.rollback()
                result.stats.errors += 1
                result.errors.append(f"{record.source_record_id}: {exc}")
        if not self.dry_run:
            await _rebind()
        return result

    async def _try_child(
        self, result: WriteResult, record: CanonicalImportRecord, label: str, coro: Any
    ) -> None:
        """Run a single child-field write; skip (don't abort the contact) if it fails.

        Field-level validation failures (e.g. an unparseable phone number) raise
        before any DB write, so the session stays usable and we keep the rest of
        the contact. A genuine DB error would abort the shared transaction and be
        caught by write_records' per-record rollback backstop.
        """
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 - one bad field must not lose the contact.
            result.stats.skipped += 1
            result.errors.append(f"{record.source_record_id}: skipped {label}: {exc}")

    async def _write_one(self, record: CanonicalImportRecord, result: WriteResult) -> None:
        identifiers = record.stable_identifiers()
        if self.dry_run:
            result.dry_run_items.append(
                DryRunItem(
                    action="upsert" if identifiers else "create",
                    display_name=record.display_name,
                    identifiers=[f"{item.namespace}:{item.value}" for item in identifiers],
                )
            )
            return

        source_id = await self._source_id(record)
        person_id: uuid.UUID
        event_id: uuid.UUID | None
        try:
            matches = await find_person_by_identifier(
                self.ctx,
                FindPersonByIdentifierInput(
                    identifiers=[
                        IdentifierInput(namespace=item.namespace, value=item.value)
                        for item in identifiers
                    ]
                    or [IdentifierInput(namespace="import_record", value=record.source_record_id)]
                ),
            )
        except ToolError as exc:
            if exc.code != AMBIGUOUS_MATCH:
                raise
            matches = None

        if matches is not None and len(matches.matches) == 1:
            upserted = await upsert_person(self.ctx, self._upsert_input(record, identifiers))
            person_id = upserted.person_id
            event_id = upserted.event_id
            result.stats.merged += 1
        elif matches is not None and not matches.matches:
            created = await create_person(self.ctx, self._create_input(record))
            person_id = created.person_id
            event_id = created.event_id
            result.stats.created += 1
        else:
            created = await create_person(self.ctx, self._create_input(record))
            person_id = created.person_id
            event_id = created.event_id
            candidate_ids = [item.person_id for item in matches.matches] if matches else []
            await self._log_merge_candidate(created.person_id, candidate_ids, record)
            result.stats.candidates_logged += 1

        await self._write_children(person_id, record, result)
        try:
            # KNOWN ISSUE (flagged for a proper fix): field_provenance's RLS WITH CHECK
            # requires the referenced action_event to be visible, but action_events are
            # written to the separate audit session and aren't visible to the main
            # session mid-transaction, so the insert is rejected. Don't lose the
            # imported contact over provenance metadata — isolate the facts writes in a
            # savepoint so their failure rolls back only the facts, not the person.
            async with self.ctx.db.begin_nested():
                await self._record_person_facts(person_id, record, source_id, event_id)
        except Exception as exc:  # noqa: BLE001
            result.stats.skipped += 1
            result.errors.append(f"{record.source_record_id}: skipped facts/provenance: {exc}")

    def _create_input(self, record: CanonicalImportRecord) -> CreatePersonInput:
        return CreatePersonInput(
            display_name=record.display_name,
            given_name=record.given_name,
            family_name=record.family_name,
            additional_names=record.additional_names,
            honorific_prefix=record.honorific_prefix,
            honorific_suffix=record.honorific_suffix,
            nicknames=record.nicknames,
            birthday=_birthday(record.birthday),
            headline=record.headline,
            occupation_title=record.occupation_title,
            tenant_membership_notes=record.notes,
            initial_tags=record.tags,
            confidence=self.provenance.confidence(),
        )

    def _upsert_input(
        self, record: CanonicalImportRecord, identifiers: list[Any]
    ) -> UpsertPersonInput:
        match_by = [
            IdentifierInput(namespace=item.namespace, value=item.value)
            for item in identifiers[:5]
        ] or [IdentifierInput(namespace="import_record", value=record.source_record_id)]
        return UpsertPersonInput(
            **self._create_input(record).model_dump(),
            match_by=match_by,
            conflict_strategy="fill_blanks",
        )

    async def _write_children(
        self, person_id: uuid.UUID, record: CanonicalImportRecord, result: WriteResult
    ) -> None:
        confidence = self.provenance.confidence()
        for email in record.emails:
            await self._try_child(
                result,
                record,
                f"email:{email.address}",
                add_email(
                    self.ctx,
                    AddEmailInput(
                        person_id=person_id,
                        address=email.address,
                        type=email.type,
                        label=email.label,
                        is_primary=email.is_primary,
                        confidence=confidence,
                    ),
                ),
            )
        for phone in record.phones:
            await self._try_child(
                result,
                record,
                f"phone:{phone.e164}",
                add_phone(
                    self.ctx,
                    AddPhoneInput(
                        person_id=person_id,
                        e164=phone.e164,
                        type=phone.type,
                        label=phone.label,
                        is_primary=phone.is_primary,
                        confidence=confidence,
                    ),
                ),
            )
        for address in record.addresses:
            await self._try_child(
                result,
                record,
                "address",
                add_address(
                    self.ctx,
                    AddAddressInput(
                        subject_kind="person",
                        subject_id=person_id,
                        confidence=confidence,
                        **address.model_dump(),
                    ),
                ),
            )
        for identifier in record.identifiers:
            try:
                await add_identifier(
                    self.ctx,
                    AddIdentifierInput(
                        subject_kind="person",
                        subject_id=person_id,
                        confidence=confidence,
                        **identifier.model_dump(),
                    ),
                )
            except ToolError as exc:
                if exc.code == DUPLICATE_RECORD:
                    continue  # already present — idempotent, not an error
                result.stats.skipped += 1
                result.errors.append(
                    f"{record.source_record_id}: skipped identifier:{identifier.namespace}: {exc}"
                )
        if record.tags:
            await self._try_child(
                result,
                record,
                "tags",
                tag_person(
                    self.ctx,
                    TagPersonInput(person_id=person_id, tags=record.tags, confidence=confidence),
                ),
            )
        for employment in record.employments:
            try:
                org_id = await self._ensure_org(employment.company)
                await set_employment(
                    self.ctx,
                    SetEmploymentInput(
                        person_id=person_id,
                        org_id=org_id,
                        role_type="employee",
                        title=employment.title,
                        department=employment.department,
                        started_at=_date(employment.started_at),
                        is_primary=employment.is_primary,
                        confidence=confidence,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - skip a bad employer, keep the contact.
                result.stats.skipped += 1
                result.errors.append(
                    f"{record.source_record_id}: skipped employment:{employment.company}: {exc}"
                )

    async def _ensure_org(self, name: str) -> uuid.UUID:
        existing = await self.ctx.db.scalar(
            select(Organization.id).where(
                Organization.canonical_owner_tenant_id == self.ctx.tenant_id,
                sa.func.lower(Organization.display_name) == name.lower(),
            )
        )
        if existing is not None:
            return existing
        created = await create_organization(
            self.ctx,
            CreateOrganizationInput(
                legal_name=name,
                display_name=name,
                confidence=self.provenance.confidence(),
            ),
        )
        return created.org_id

    async def _source_id(self, record: CanonicalImportRecord) -> uuid.UUID:
        existing = await self.ctx.db.scalar(
            select(sources_table.c.id).where(
                sources_table.c.tenant_id == self.ctx.tenant_id,
                sources_table.c.source_type == self.provenance.source_type,
                sources_table.c.source_uri == self.provenance.source_uri,
                sources_table.c.source_record_id == record.source_record_id,
            )
        )
        if isinstance(existing, uuid.UUID):
            return existing
        source_id = uuid.uuid4()
        await self.ctx.db.execute(
            sources_table.insert().values(
                id=source_id,
                source_type=self.provenance.source_type,
                source_uri=self.provenance.source_uri,
                source_record_id=record.source_record_id,
                retrieval_method="importer",
                source_reliability_base=Decimal(str(self.provenance.source_reliability_base)),
                tenant_id=self.ctx.tenant_id,
            )
        )
        return source_id

    async def _record_person_facts(
        self,
        person_id: uuid.UUID,
        record: CanonicalImportRecord,
        source_id: uuid.UUID,
        event_id: uuid.UUID | None,
    ) -> None:
        for path, value in _facts(record):
            await record_fact(
                self.ctx,
                RecordFactInput(
                    subject_kind="person",
                    subject_id=person_id,
                    predicate=path,
                    object_kind="literal",
                    object_value=value,
                    source_type=self.provenance.source_type,
                    source_uri=self.provenance.source_uri,
                    source_record_id=record.source_record_id,
                    source_id=source_id,
                    confidence=self.provenance.confidence(),
                    observed_at=self.provenance.observed_at,
                    set_by_event_id=event_id,
                ),
            )

    async def _log_merge_candidate(
        self,
        source_person_id: uuid.UUID,
        candidate_ids: list[uuid.UUID],
        record: CanonicalImportRecord,
    ) -> None:
        await self.ctx.db.execute(
            merge_candidates_table.insert().values(
                tenant_id=self.ctx.tenant_id,
                source_person_id=source_person_id,
                candidate_person_ids=[str(item) for item in candidate_ids],
                source_type=self.provenance.source_type,
                source_record_id=record.source_record_id,
                confidence=Decimal(str(self.dedup_threshold)),
                reason="ambiguous import match",
            )
        )


class RecordFactInput(BaseModel):
    subject_kind: Literal["person", "organization"]
    subject_id: uuid.UUID
    predicate: str
    object_kind: Literal["literal", "person", "organization", "address", "url"]
    object_value: Any
    source_type: str
    source_uri: str | None = None
    source_record_id: str | None = None
    source_id: uuid.UUID | None = None
    confidence: float
    observed_at: datetime
    set_by_event_id: uuid.UUID | None = None


class RecordFactOutput(BaseModel):
    fact_id: uuid.UUID


async def record_fact(ctx: MCPContext, req: RecordFactInput) -> RecordFactOutput:
    source_id = req.source_id
    if source_id is None:
        source_id = uuid.uuid4()
        await ctx.db.execute(
            sources_table.insert().values(
                id=source_id,
                source_type=req.source_type,
                source_uri=req.source_uri,
                source_record_id=req.source_record_id,
                source_reliability_base=Decimal(str(req.confidence)),
                tenant_id=ctx.tenant_id,
            )
        )
    fact_id = uuid.uuid4()
    await ctx.db.execute(
        facts_table.insert().values(
            id=fact_id,
            tenant_id=ctx.tenant_id,
            subject_kind=req.subject_kind,
            subject_id=req.subject_id,
            predicate=req.predicate,
            object_kind=req.object_kind,
            object_value=req.object_value,
            source_id=source_id,
            confidence=Decimal(str(req.confidence)),
            observed_at=req.observed_at,
        )
    )
    await ctx.db.execute(
        sa.dialects.postgresql.insert(field_provenance_table)
        .values(
            entity_type=req.subject_kind,
            entity_id=req.subject_id,
            field_path=req.predicate,
            current_value=req.object_value,
            set_by_event_id=req.set_by_event_id,
            set_by_actor=ctx.actor_chain,
            source=req.source_type,
            source_record_id=req.source_record_id,
            confidence=Decimal(str(req.confidence)),
            established_at=req.observed_at,
            history=[],
        )
        .on_conflict_do_update(
            index_elements=["entity_type", "entity_id", "field_path"],
            set_={
                "current_value": req.object_value,
                "set_by_event_id": req.set_by_event_id,
                "set_by_actor": ctx.actor_chain,
                "source": req.source_type,
                "source_record_id": req.source_record_id,
                "confidence": Decimal(str(req.confidence)),
                "established_at": req.observed_at,
            },
        )
    )
    return RecordFactOutput(fact_id=fact_id)


def _facts(record: CanonicalImportRecord) -> list[tuple[str, Any]]:
    facts: list[tuple[str, Any]] = [
        ("display_name", record.display_name),
    ]
    for field_name in ["given_name", "family_name", "headline", "occupation_title", "birthday"]:
        value = getattr(record, field_name)
        if value:
            facts.append((field_name, value))
    facts.extend(
        (f"emails[{index}].address", email.address) for index, email in enumerate(record.emails)
    )
    facts.extend((f"phones[{index}].e164", phone.e164) for index, phone in enumerate(record.phones))
    facts.extend(
        (f"identifiers[{index}].{identifier.namespace}", identifier.value)
        for index, identifier in enumerate(record.identifiers)
    )
    return facts


def _birthday(value: dict[str, int | None] | None) -> Birthday | None:
    if value is None:
        return None
    return Birthday(year=value.get("year"), month=value.get("month"), day=value.get("day"))


def _date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None
