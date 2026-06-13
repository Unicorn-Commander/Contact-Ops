"""GDPR compliance MCP tools — Art.15 export + Art.17 erasure (P-00075).

Four ADMIN-only tools over the dormant compliance engine (services live in
``contact_ops/services/compliance_export.py`` + ``compliance_erasure.py``):

  * ``export_subject_data``  — Art.15 data subject access: gather one person's
    record + related rows into a JSON package, upload to Garage, return a signed
    download URL (inline-doc fallback when Garage is unconfigured).
  * ``export_tenant_data``   — the tenant-wide Art.15 package.
  * ``erase_subject``        — Art.17 right-to-erasure for one person. TWO-PHASE
    when ``COMPLIANCE_ENGINE_ENABLED`` (tombstone now + a 30-day grace, the sweep
    purges later); single-phase immediate purge otherwise. Requires the literal
    confirmation phrase ``ERASE THIS PERSON PERMANENTLY``.
  * ``erase_tenant_account`` — Art.17 for the whole tenant (every person purged +
    ledger anonymized in place). Requires the stronger phrase
    ``ERASE ENTIRE TENANT ACCOUNT PERMANENTLY``.

DORMANT-BY-DEFAULT (mirrors the entitlement/billing "present but OFF" pattern):
these tools are registered so the manifest + entitlement map can reference them,
but ``COMPLIANCE_ENGINE_ENABLED`` (default False) chooses between the two-phase
tombstone flow (ON) and the immediate single-phase purge (OFF). The Settings flag
ships safe-off; the tenant gating happens via entitlement (these tool names go in
``entitlement._ENTERPRISE_TOOLS``, wired separately).

Erasure note (Art.17 exception): the hash-chained ``action_event`` ledger is
NEVER deleted — PII inside it is redacted in place and the content_hash is left
intact (a redacted event lawfully won't re-verify, the chain links survive). The
purge tools delegate that to ``anonymize_subject_action_events`` in the service.
"""
# ruff: noqa: I001

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from contact_ops.core.config import get_settings
from contact_ops.mcp.errors import CONFIRMATION_PHRASE_MISMATCH, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import register
from contact_ops.services import compliance_erasure, compliance_export

# Literal confirmation phrases — typo-proof guardrails on the irreversible tools.
ERASE_SUBJECT_PHRASE = "ERASE THIS PERSON PERMANENTLY"
ERASE_TENANT_PHRASE = "ERASE ENTIRE TENANT ACCOUNT PERMANENTLY"


# ---------------------------------------------------------------------------
# export_subject_data (Art.15 — single data subject)
# ---------------------------------------------------------------------------


class ExportSubjectDataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID


class ExportSubjectDataOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    person_id: uuid.UUID
    download_url: str | None = None
    expires_at: datetime | None = None
    inline: dict[str, Any] | None = None
    status: str = "applied"


async def export_subject_data(
    ctx: MCPContext, req: ExportSubjectDataInput
) -> ExportSubjectDataOutput:
    """Assemble + deliver one person's Art.15 data subject access package.

    Delegates to ``compliance_export.export_subject_data``, which gathers the
    person + related rows into JSON and uploads to Garage (returning a signed
    download URL). When Garage is unconfigured the service returns an
    ``{inline: doc}`` fallback so a self-host/dev deployment still works.
    """
    require_role(ctx, "ADMIN")
    require_scopes(ctx, ("person:read",))

    result = await compliance_export.export_subject_data(
        ctx.db,
        ctx.tenant_id,
        req.person_id,
        settings=get_settings(),
    )
    return ExportSubjectDataOutput(
        person_id=req.person_id,
        download_url=result.get("download_url"),
        expires_at=result.get("expires_at"),
        inline=result.get("inline"),
        status=result.get("status", "applied"),
    )


# ---------------------------------------------------------------------------
# export_tenant_data (Art.15 — whole tenant)
# ---------------------------------------------------------------------------


class ExportTenantDataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportTenantDataOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    tenant_id: uuid.UUID
    download_url: str | None = None
    expires_at: datetime | None = None
    inline: dict[str, Any] | None = None
    person_count: int | None = None
    status: str = "applied"


async def export_tenant_data(
    ctx: MCPContext, req: ExportTenantDataInput
) -> ExportTenantDataOutput:
    """Assemble + deliver the tenant-wide Art.15 export package."""
    require_role(ctx, "ADMIN")
    require_scopes(ctx, ("person:read",))

    result = await compliance_export.export_tenant_data(
        ctx.db,
        ctx.tenant_id,
        settings=get_settings(),
    )
    return ExportTenantDataOutput(
        tenant_id=ctx.tenant_id,
        download_url=result.get("download_url"),
        expires_at=result.get("expires_at"),
        inline=result.get("inline"),
        person_count=result.get("person_count"),
        status=result.get("status", "applied"),
    )


# ---------------------------------------------------------------------------
# erase_subject (Art.17 — single person, two-phase when engine enabled)
# ---------------------------------------------------------------------------


class EraseSubjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)
    confirmation_phrase: Literal["ERASE THIS PERSON PERMANENTLY"]


class EraseSubjectOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    person_id: uuid.UUID
    phase: str  # "tombstoned" (two-phase) | "purged" (immediate)
    tombstoned_at: datetime | None = None
    purge_after: datetime | None = None
    cascade_summary: dict[str, int] | None = None
    status: str = "applied"


async def erase_subject(
    ctx: MCPContext, req: EraseSubjectInput
) -> EraseSubjectOutput:
    """Erase (Art.17) one data subject.

    Two-phase when ``COMPLIANCE_ENGINE_ENABLED``: phase 1 tombstones the person
    (redacts direct PII on the row, sets tombstoned_at/purge_after = now + grace),
    and the retention sweep purges after the grace window. When the engine flag is
    OFF the tool falls back to an IMMEDIATE single-phase purge (hard-delete
    operational PII + anonymize the ledger in place). Both paths leave the
    hash-chained ledger intact (PII redacted, content_hash untouched).
    """
    require_role(ctx, "ADMIN")
    require_scopes(ctx, ("person:delete",))

    if req.confirmation_phrase != ERASE_SUBJECT_PHRASE:
        raise ToolError(
            CONFIRMATION_PHRASE_MISMATCH,
            "confirmation phrase mismatch",
            retryable=False,
            hint=f"send confirmation_phrase exactly: {ERASE_SUBJECT_PHRASE!r}",
        )

    settings = get_settings()
    if settings.COMPLIANCE_ENGINE_ENABLED:
        # Phase 1: tombstone + 30-day (config) grace; the sweep purges later.
        result = await compliance_erasure.tombstone_person(
            ctx.db,
            ctx.tenant_id,
            req.person_id,
            reason=req.reason,
            grace_days=settings.ERASURE_GRACE_DAYS,
        )
        return EraseSubjectOutput(
            person_id=req.person_id,
            phase="tombstoned",
            tombstoned_at=result.get("tombstoned_at"),
            purge_after=result.get("purge_after"),
            status=result.get("status", "applied"),
        )

    # Engine OFF: immediate single-phase hard purge.
    result = await compliance_erasure.purge_person(
        ctx.db,
        ctx.tenant_id,
        req.person_id,
    )
    return EraseSubjectOutput(
        person_id=req.person_id,
        phase="purged",
        cascade_summary=result.get("cascade_summary"),
        status=result.get("status", "applied"),
    )


# ---------------------------------------------------------------------------
# erase_tenant_account (Art.17 — whole tenant; strongest confirmation)
# ---------------------------------------------------------------------------


class EraseTenantAccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_phrase: Literal["ERASE ENTIRE TENANT ACCOUNT PERMANENTLY"]


class EraseTenantAccountOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    tenant_id: uuid.UUID
    persons_purged: int | None = None
    events_anonymized: int | None = None
    cascade_summary: dict[str, int] | None = None
    status: str = "applied"


async def erase_tenant_account(
    ctx: MCPContext, req: EraseTenantAccountInput
) -> EraseTenantAccountOutput:
    """Erase (Art.17) the entire tenant account.

    Purges every person in the tenant, anonymizes the ledger in place, and does
    best-effort external-store purge (Garage/Qdrant/FalkorDB). Guarded by the
    strongest literal confirmation phrase. This is irreversible regardless of the
    engine flag — there is no tombstone/grace for a whole-account erasure.
    """
    require_role(ctx, "ADMIN")
    require_scopes(ctx, ("person:delete",))

    if req.confirmation_phrase != ERASE_TENANT_PHRASE:
        raise ToolError(
            CONFIRMATION_PHRASE_MISMATCH,
            "confirmation phrase mismatch",
            retryable=False,
            hint=f"send confirmation_phrase exactly: {ERASE_TENANT_PHRASE!r}",
        )

    result = await compliance_erasure.erase_tenant_account(ctx.db, ctx.tenant_id)
    return EraseTenantAccountOutput(
        tenant_id=ctx.tenant_id,
        persons_purged=result.get("persons_purged"),
        events_anonymized=result.get("events_anonymized"),
        cascade_summary=result.get("cascade_summary"),
        status=result.get("status", "applied"),
    )


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


def register_compliance_tools() -> None:
    register(
        name="export_subject_data",
        description=(
            "GDPR Art.15 data subject access: package one person's record plus "
            "related data into JSON, upload to Garage, and return a signed "
            "download URL (inline fallback if Garage is unconfigured). ADMIN "
            "only; requires person:read."
        ),
        input_model=ExportSubjectDataInput,
        output_model=ExportSubjectDataOutput,
        handler=export_subject_data,
        required_role="ADMIN",
        required_scopes=("person:read",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="export_tenant_data",
        description=(
            "GDPR Art.15 tenant-wide export: package every person in the tenant "
            "into a JSON archive, upload to Garage, and return a signed download "
            "URL (inline fallback if Garage is unconfigured). ADMIN only; "
            "requires person:read."
        ),
        input_model=ExportTenantDataInput,
        output_model=ExportTenantDataOutput,
        handler=export_tenant_data,
        required_role="ADMIN",
        required_scopes=("person:read",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="erase_subject",
        description=(
            "GDPR Art.17 erasure of one person. Two-phase when the compliance "
            "engine is enabled (tombstone now, purge after a grace window); "
            "immediate hard purge otherwise. Hard-deletes operational PII and "
            "anonymizes the hash-chained ledger IN PLACE (never deletes ledger "
            "rows). Irreversible. Requires the literal confirmation phrase "
            "'ERASE THIS PERSON PERMANENTLY'. ADMIN only; requires person:delete."
        ),
        input_model=EraseSubjectInput,
        output_model=EraseSubjectOutput,
        handler=erase_subject,
        required_role="ADMIN",
        required_scopes=("person:delete",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="erase_tenant_account",
        description=(
            "GDPR Art.17 erasure of the ENTIRE tenant account: purge every "
            "person, anonymize the ledger in place, and best-effort purge "
            "external stores. Irreversible, no grace window. Requires the "
            "strong literal confirmation phrase 'ERASE ENTIRE TENANT ACCOUNT "
            "PERMANENTLY'. ADMIN only; requires person:delete."
        ),
        input_model=EraseTenantAccountInput,
        output_model=EraseTenantAccountOutput,
        handler=erase_tenant_account,
        required_role="ADMIN",
        required_scopes=("person:delete",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="none",
    )


register_compliance_tools()
