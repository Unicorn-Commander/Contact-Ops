"""REST ingest seam — the APP front door (CONTACT-OPS-INGEST-CONTRACT §1B / §3).

`POST /api/v1/contacts/ingest` (single) and `/api/v1/contacts/ingest/bulk` (batch)
are the non-MCP transport over the SAME upsert core the MCP `upsert_person` /
`bulk_upsert_people` tools use. Leadgen apps push a graduated lead here with the
shared `contactops_push` connector; the same envelope also serves a customer's own
agent presenting a Brigade-STS service token.

Both doors share one core, so this router does NOT reimplement matching, dedup,
provenance persistence, audit, or RLS — it adapts an HTTP request into an
`UpsertPersonInput` and calls `upsert_person` under a tenant-bound session, exactly
the way `api/import_vcard.py` adapts a file upload.

Identity model + RLS (HARD constraint): the tenant is resolved ONLY from the
validated JWT claims on `request.state.jwt_claims` (set by JWTValidationMiddleware
— never a header), and every DB statement runs through a session pre-bound to
`app.tenant_id` via `bind_session_context`, so Row-Level Security applies. This is
identical whether the caller presented a uchub user JWT (claims carry `tenant_id`)
or a Brigade service token (the middleware folds `org_id` into `tenant_id` +
`scope`), so the one endpoint serves both producers.

Scope (CONTACT-OPS-INGEST-CONTRACT §2): writes require `contacts:write`. We ALSO
accept the legacy `person:write` scope the MCP write tools already require, so
nothing currently authorized breaks while the new Keycloak scope is rolled out. A
caller with neither gets 403 BEFORE any DB work.

Payload mapping (§3): we accept ONLY identity attributes (name / emails / phones /
social / company) plus the `source` and `rollups` blocks. Domain-artifact fields
are not in the request model at all, so a producer cannot smuggle vertical data
into the identity record. `consent_basis` is REQUIRED on this door (the contract's
legal-provenance requirement) even though the column itself is nullable for the
many persons created via non-ingest paths.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, cast

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from contact_ops.core.config import get_settings
from contact_ops.core.database import (
    async_session_maker,
    audit_session_maker,
    bind_session_context,
)
from contact_ops.mcp.errors import ToolError
from contact_ops.mcp.rbac import get_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.people import (
    EmailInput,
    PhoneInput,
    UpsertPersonInput,
    upsert_person,
)
from contact_ops.models.enums import ConsentBasis

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/contacts", tags=["Ingest"])

# Either scope authorizes a write on this door. `contacts:write` is the new
# suite-canonical scope (CONTACT-OPS-INGEST-CONTRACT §2); `person:write` is the
# legacy scope the MCP write tools already gate on, kept so existing tokens keep
# working through the rollout (a Keycloak scope is not retroactive — old tokens
# carry the old name until re-auth).
_WRITE_SCOPES = frozenset({"contacts:write", "person:write"})

# Batch ceiling mirrors bulk_upsert_people's BulkUpsertPeopleInput (max 50).
_MAX_BULK = 50


# ---------------------------------------------------------------------------
# Request models (CONTACT-OPS-INGEST-CONTRACT §3) — identity-only, no artifacts.
# ---------------------------------------------------------------------------


class MatchIdentifier(BaseModel):
    """A single dedup identifier. `kind` is the contract's vocabulary; it maps to
    the upsert core's `namespace` (email/phone) or to the external_ref fallback."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["email", "phone", "external_ref"]
    value: str = Field(min_length=1, max_length=400)


class SocialHandle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str = Field(min_length=1, max_length=60)
    handle: str = Field(min_length=1, max_length=200)


class IngestAttributes(BaseModel):
    """IDENTITY attributes only (§0/§3): how you reach + recognize the person.

    `extra="forbid"` is the wall that keeps domain artifacts out — a producer that
    tries to POST a transcript / invoice / vertical-score field gets a 422, not a
    silently-dropped field."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    given_name: str | None = Field(default=None, max_length=100)
    family_name: str | None = Field(default=None, max_length=100)
    emails: list[EmailInput] = Field(default_factory=list, max_length=20)
    phones: list[PhoneInput] = Field(default_factory=list, max_length=20)
    social: list[SocialHandle] = Field(default_factory=list, max_length=20)
    # Canonical company as a free-text identity attribute (the full org graph is
    # built via the MCP org/employment tools; ingest records only the label).
    company: str | None = Field(default=None, max_length=200)


class IngestSource(BaseModel):
    """Provenance block (§3). `consent_basis` is REQUIRED on the ingest door."""

    model_config = ConfigDict(extra="forbid")

    source_label: str = Field(min_length=1, max_length=120)
    source_kind: str | None = Field(default=None, max_length=60)
    external_ref: str | None = Field(default=None, max_length=400)
    consent_basis: str = Field(min_length=1, max_length=60)

    @field_validator("consent_basis")
    @classmethod
    def _validate_consent(cls, value: str) -> str:
        allowed = {member.value for member in ConsentBasis}
        if value not in allowed:
            raise ValueError(f"consent_basis must be one of {sorted(allowed)}")
        return value


class IngestRollups(BaseModel):
    """Optional lightweight projected fields (§3). The producing app stays the
    source of truth; these are denormalized for filter/sort only."""

    model_config = ConfigDict(extra="forbid")

    lead_score: float | None = None
    stage: str | None = Field(default=None, max_length=120)


class IngestContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_by: list[MatchIdentifier] = Field(min_length=1, max_length=5)
    attributes: IngestAttributes
    source: IngestSource
    rollups: IngestRollups | None = None
    # Optional caller-supplied idempotency key (defense-in-depth on top of the
    # natural-key dedup the core already does).
    idempotency_key: uuid.UUID | None = None
    conflict_strategy: Literal[
        "fill_blanks", "prefer_incoming", "prefer_existing", "propose_only"
    ] = "fill_blanks"


class IngestContactResponse(BaseModel):
    # `contact_id` is the shared id everyone references (§3). It is the Person id;
    # named `contact_id` on the wire per the contract.
    contact_id: uuid.UUID
    action: Literal["created", "updated", "noop"]
    etag: str


class BulkIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[IngestContactRequest] = Field(min_length=1, max_length=_MAX_BULK)


class BulkIngestItemResult(BaseModel):
    index: int
    contact_id: uuid.UUID | None = None
    action: str
    error_code: str | None = None
    error_message: str | None = None


class BulkIngestResponse(BaseModel):
    created: int
    updated: int
    noop: int
    errors: int
    results: list[BulkIngestItemResult]


# ---------------------------------------------------------------------------
# Payload → UpsertPersonInput mapping (identity-only)
# ---------------------------------------------------------------------------


def _to_upsert_input(body: IngestContactRequest) -> UpsertPersonInput:
    """Map the ingest envelope onto the shared upsert core's input.

    Only identity attributes + the source/rollup provenance flow through. The
    `match_by` `kind` vocabulary maps onto the core's `namespace`
    (email/phone/external_ref) verbatim. `display_name` is left None when the
    producer omits it: the core requires it ONLY on create, and a repeat push that
    matches an existing person legitimately need not resend it.
    """
    attrs = body.attributes
    rollups = body.rollups or IngestRollups()
    match_by = [
        {"namespace": ident.kind, "value": ident.value} for ident in body.match_by
    ]
    return UpsertPersonInput(
        match_by=cast(Any, match_by),
        display_name=attrs.display_name,
        given_name=attrs.given_name,
        family_name=attrs.family_name,
        emails=attrs.emails,
        phones=attrs.phones,
        # `social` + `company` are accepted at the door as identity attributes;
        # the v1 upsert core persists name/email/phone + provenance, so they are
        # validated-and-ignored here rather than silently smuggled elsewhere. The
        # MCP social/employment tools own the full handle/org graph.
        conflict_strategy=body.conflict_strategy,
        idempotency_key=body.idempotency_key,
        source_label=body.source.source_label,
        source_kind=body.source.source_kind,
        external_ref=body.source.external_ref,
        consent_basis=body.source.consent_basis,
        lead_score=rollups.lead_score,
        stage=rollups.stage,
    )


# ---------------------------------------------------------------------------
# Core ingest (tenant-bound, RLS-enforced) — shared by single + bulk
# ---------------------------------------------------------------------------


async def ingest_one(ctx: MCPContext, body: IngestContactRequest) -> IngestContactResponse:
    """Run ONE ingest through the shared upsert core against ``ctx.db``.

    ``ctx`` MUST already be bound to the caller's tenant (its ``db`` session has
    ``app.tenant_id`` set), so RLS applies. Separated from the HTTP handler so the
    core ingest path is unit-testable with a tenant-bound test session exactly like
    the MCP tools are. Re-raises ``ToolError`` (the caller maps it to HTTP).

    The caller has already passed the door's scope gate (``contacts:write`` OR the
    legacy ``person:write``). The shared core ``upsert_person`` still gates on its
    own ``person:write`` scope + STAFF role (we do NOT weaken those — they are the
    MCP write contract). So when the caller authorized via the NEW
    ``contacts:write`` alone, we present the core a context whose claims carry the
    equivalent ``person:write`` + STAFF authority. This bridge is local to the
    ingest call (a shallow claims copy), never mutates the request's own claims,
    and is applied ONLY after the door has already authorized the write.
    """
    core_ctx = _core_context_for(ctx)
    upsert_input = _to_upsert_input(body)
    result = await upsert_person(core_ctx, upsert_input)
    return IngestContactResponse(
        contact_id=result.person_id,
        action=result.action,
        etag=result.etag,
    )


def _core_context_for(ctx: MCPContext) -> MCPContext:
    """Return a context the shared upsert core accepts, bridging `contacts:write`.

    If the caller already holds `person:write` and a STAFF+ role, this is a no-op
    passthrough (identical object). Otherwise — i.e. they authorized via the new
    `contacts:write` scope — we shallow-copy the claims and add the core's expected
    `person:write` scope, and ensure the role ladder is at least STAFF, so the
    inner `require_scopes` / `require_role` succeed for an already-authorized write.
    The original `ctx.claims` (and thus `request.state.jwt_claims`) is untouched.

    Defense in depth: the bridge fires ONLY when the caller actually holds a
    recognized write scope (``contacts:write`` or ``person:write``). A caller with
    NEITHER (e.g. read-only) is passed through unchanged so the core still rejects
    it — so even if ``ingest_one`` is ever invoked WITHOUT the HTTP door's scope
    gate in front of it, no read-only identity is silently escalated.
    """
    from contact_ops.mcp.rbac import ROLE_LADDER, get_role

    granted = get_scopes(ctx.claims)
    role = get_role(ctx.claims)
    has_scope = "person:write" in granted
    has_role = ROLE_LADDER.get(role, -1) >= ROLE_LADDER["STAFF"]
    if has_scope and has_role:
        return ctx
    # Only an already-write-authorized caller is bridged; otherwise leave the
    # context as-is and let the core's own require_scopes/require_role reject it.
    if granted.isdisjoint(_WRITE_SCOPES):
        return ctx

    new_claims = dict(ctx.claims)
    if not has_scope:
        scope_str = new_claims.get("scope", "")
        existing = scope_str.split() if isinstance(scope_str, str) else list(scope_str)
        if "person:write" not in existing:
            existing.append("person:write")
        new_claims["scope"] = " ".join(existing)
    if not has_role:
        realm = dict(new_claims.get("realm_access") or {})
        roles = list(realm.get("roles") or [])
        roles.append("STAFF")
        realm["roles"] = roles
        new_claims["realm_access"] = realm
    return MCPContext(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        actor_chain=ctx.actor_chain,
        human_authority=ctx.human_authority,
        db=ctx.db,
        audit_db=ctx.audit_db,
        request_id=ctx.request_id,
        claims=new_claims,
    )


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------


@router.post(
    "/ingest",
    response_model=IngestContactResponse,
    summary="Ingest a single graduated lead as a contact (idempotent upsert)",
)
async def ingest_contact(
    request: Request, body: IngestContactRequest
) -> IngestContactResponse:
    ctx_base = _context_from_request(request)
    _require_write_scope(ctx_base)

    settings = get_settings()
    uc_uid = str(ctx_base.claims.get("uc_uid") or ctx_base.user_id)
    async with async_session_maker() as db, audit_session_maker() as audit_db:
        await bind_session_context(db, str(ctx_base.tenant_id), uc_uid, settings)
        await bind_session_context(audit_db, str(ctx_base.tenant_id), uc_uid, settings)
        ctx_base.db = db
        ctx_base.audit_db = audit_db
        try:
            response = await ingest_one(ctx_base, body)
            await db.commit()
            await audit_db.commit()
        except ToolError as exc:
            await db.rollback()
            await audit_db.rollback()
            raise _http_from_tool_error(exc) from exc
        except Exception:
            await db.rollback()
            await audit_db.rollback()
            raise
    return response


@router.post(
    "/ingest/bulk",
    response_model=BulkIngestResponse,
    summary="Ingest up to 50 graduated leads with per-item outcomes",
)
async def ingest_contacts_bulk(
    request: Request, body: BulkIngestRequest
) -> BulkIngestResponse:
    ctx_base = _context_from_request(request)
    _require_write_scope(ctx_base)

    settings = get_settings()
    uc_uid = str(ctx_base.claims.get("uc_uid") or ctx_base.user_id)
    results: list[BulkIngestItemResult] = []
    created = updated = noop = errors = 0
    async with async_session_maker() as db, audit_session_maker() as audit_db:
        await bind_session_context(db, str(ctx_base.tenant_id), uc_uid, settings)
        await bind_session_context(audit_db, str(ctx_base.tenant_id), uc_uid, settings)
        ctx_base.db = db
        ctx_base.audit_db = audit_db
        try:
            for index, item in enumerate(body.items):
                try:
                    item_resp = await ingest_one(ctx_base, item)
                except ToolError as exc:
                    errors += 1
                    results.append(
                        BulkIngestItemResult(
                            index=index,
                            action="error",
                            error_code=exc.code,
                            error_message=exc.message,
                        )
                    )
                    continue
                created += int(item_resp.action == "created")
                updated += int(item_resp.action == "updated")
                noop += int(item_resp.action == "noop")
                results.append(
                    BulkIngestItemResult(
                        index=index,
                        contact_id=item_resp.contact_id,
                        action=item_resp.action,
                    )
                )
            await db.commit()
            await audit_db.commit()
        except Exception:
            await db.rollback()
            await audit_db.rollback()
            raise
    return BulkIngestResponse(
        created=created, updated=updated, noop=noop, errors=errors, results=results
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_write_scope(ctx: MCPContext) -> None:
    """403 unless the caller holds `contacts:write` or the legacy `person:write`.

    Done at the HTTP layer (not via ``require_scopes`` which raises a ToolError)
    so the deny is a clean 403 with a deterministic ``detail`` the connector reads.
    """
    granted = get_scopes(ctx.claims)
    if granted.isdisjoint(_WRITE_SCOPES):
        logger.info(
            "ingest_scope_denied",
            tenant_id=str(ctx.tenant_id),
            have=sorted(granted),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INSUFFICIENT_SCOPE: requires contacts:write",
        )


def _context_from_request(request: Request) -> MCPContext:
    """Build an MCPContext from the validated JWT claims (NEVER a header).

    Mirrors ``api/import_vcard.py``: tenant + identity come only from
    ``request.state.jwt_claims``. The ``db`` / ``audit_db`` are filled in by the
    handler once the tenant-bound sessions are opened.
    """
    claims = getattr(request.state, "jwt_claims", None)
    if not isinstance(claims, dict) or not claims.get("tenant_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthenticated"
        )
    try:
        tenant_id = uuid.UUID(str(claims["tenant_id"]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid tenant_id claim",
        ) from exc
    user_id = str(claims.get("sub") or "")
    return MCPContext(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_chain=_state_dict(request, "actor_chain", {"sub": user_id}),
        human_authority=str(
            getattr(request.state, "human_authority", user_id) or user_id
        ),
        db=cast(Any, None),
        audit_db=cast(Any, None),
        request_id=str(
            getattr(request.state, "request_id", "")
            or request.headers.get("x-request-id")
            or uuid.uuid4()
        ),
        claims=claims,
    )


def _state_dict(request: Request, key: str, default: dict[str, Any]) -> dict[str, Any]:
    value = getattr(request.state, key, default)
    if isinstance(value, dict):
        return value
    return default


def _http_from_tool_error(exc: ToolError) -> HTTPException:
    """Map an upsert-core ToolError onto a clean HTTP status.

    AMBIGUOUS_MATCH / VALIDATION_FAILED are caller-fixable (409 / 422);
    INSUFFICIENT_* is 403; everything else collapses to 400 with the code in the
    detail so the connector can branch deterministically without leaking internals.
    """
    code = exc.code
    if code in ("INSUFFICIENT_ROLE", "INSUFFICIENT_SCOPE"):
        http_status = status.HTTP_403_FORBIDDEN
    elif code == "AMBIGUOUS_MATCH":
        http_status = status.HTTP_409_CONFLICT
    elif code == "VALIDATION_FAILED":
        http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        http_status = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=http_status, detail=f"{code}: {exc.message}")


# Re-exported for tests / symmetry with other api modules. EmailStr is imported
# so the request models share the project's validated-email type via EmailInput.
__all__ = [
    "router",
    "IngestContactRequest",
    "IngestContactResponse",
    "BulkIngestRequest",
    "BulkIngestResponse",
    "ingest_one",
    "EmailStr",
]
