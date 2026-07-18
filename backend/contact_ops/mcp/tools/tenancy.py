"""Tenancy MCP tools (Phase 2 Codex 1).

Design authority: Contact-Ops-MCP-Design.md §5.12. Operates on the ``tenants``,
``person_tenant_membership``, and ``organization_tenant_membership`` tables.

Tenant resolution rules:

* Reads use ``ctx.tenant_id`` directly (RLS enforces visibility).
* Cross-tenant operations require platform ADMIN. STAFF/MANAGER can only act
  on a target_tenant_id equal to ``ctx.tenant_id``.
* ``create_tenant`` is a platform ADMIN operation. The new tenant gets the
  caller as ``owner_user_id`` only if no explicit owner is supplied; the brief
  requires an explicit owner so we honor that.
"""
# ruff: noqa: I001

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select, text

from contact_ops.billing.plan_admin import provision_lago_customer
from contact_ops.core.config import get_settings
from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import (
    CONFIRMATION_PHRASE_MISMATCH,
    INSUFFICIENT_ROLE,
    PERSON_NOT_FOUND,
    TENANT_MISMATCH,
    VALIDATION_FAILED,
    ToolError,
)
from contact_ops.mcp.idempotency import check_or_register, store_result
from contact_ops.mcp.rbac import get_role, require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register, stable_hash
from contact_ops.models import (
    Organization,
    OrganizationTenantMembership,
    Person,
    PersonTenantMembership,
    Tenant,
)
from contact_ops.models.enums import ConsentBasis, TenantKind
from contact_ops.services import compliance_erasure, keycloak_admin

logger = structlog.get_logger(__name__)

SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")
TENANT_NOT_FOUND = "TENANT_NOT_FOUND"
MEMBERSHIP_NOT_FOUND = "MEMBERSHIP_NOT_FOUND"
HIPAA_TENANT_REQUIRES_CONSENT_BASIS = "HIPAA_TENANT_REQUIRES_CONSENT_BASIS"
HIPAA_MODE_CANNOT_DISABLE = "HIPAA_MODE_CANNOT_DISABLE"
SLUG_TAKEN = "SLUG_TAKEN"
ORG_NOT_FOUND = "ORG_NOT_FOUND"

GRAPH_MODES = ("shared", "per_org_graph", "per_org_instance")
TENANT_KINDS = tuple(kind.value for kind in TenantKind)
MEMBERSHIP_VISIBILITIES = ("visible", "hidden", "archived")
CONSENT_BASES = tuple(member.value for member in ConsentBasis)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_target_tenant(ctx: MCPContext, target_tenant_id: uuid.UUID) -> None:
    """STAFF/MANAGER cannot reach across tenants; ADMIN can."""
    if target_tenant_id == ctx.tenant_id:
        return
    role_roles = ctx.claims.get("realm_access", {}).get("roles", [])
    roles_upper = {str(role).upper() for role in role_roles}
    if "ADMIN" not in roles_upper:
        raise ToolError(
            TENANT_MISMATCH,
            "target tenant does not match caller's tenant",
            hint="ADMIN role required for cross-tenant operations",
        )


async def _load_tenant(ctx: MCPContext, tenant_id: uuid.UUID) -> Tenant:
    tenant = await ctx.db.get(Tenant, tenant_id)
    if tenant is None:
        raise ToolError(TENANT_NOT_FOUND, f"tenant {tenant_id} not found")
    return tenant


def _tenant_summary(tenant: Tenant) -> dict[str, Any]:
    return {
        "tenant_id": str(tenant.id),
        "slug": tenant.slug,
        "kind": tenant.kind.value,
        "display_name": tenant.display_name,
        "owner_user_id": str(tenant.owner_user_id),
        "parent_tenant_id": (
            str(tenant.parent_tenant_id) if tenant.parent_tenant_id else None
        ),
        "hipaa_mode": tenant.hipaa_mode,
        "gdpr_mode": tenant.gdpr_mode,
        "data_residency": tenant.data_residency,
        "carddav_enabled": tenant.carddav_enabled,
        "data_intel_publish_consent": tenant.data_intel_publish_consent,
        "graph_mode": tenant.graph_mode,
        "graph_name": tenant.graph_name,
        "qdrant_namespace": tenant.qdrant_namespace,
        "garage_bucket_prefix": tenant.garage_bucket_prefix,
        "branding": dict(tenant.branding or {}),
        "retention_policy": dict(tenant.retention_policy or {}),
        "etag": tenant.etag,
        "created_at": tenant.created_at,
        "updated_at": tenant.updated_at,
        "deprecated_at": tenant.deprecated_at,
    }


def _tenant_label_only(tenant: Tenant, role: str | None) -> dict[str, Any]:
    """Label-only projection for a non-home/non-child membership workspace.

    Per design §2.4, never expose infra namespaces (graph_name, qdrant_namespace,
    garage_bucket_prefix, owner_user_id, retention_policy) for a workspace the
    caller only reaches via user_tenant_membership.
    """
    return {
        "tenant_id": str(tenant.id),
        "slug": tenant.slug,
        "display_name": tenant.display_name,
        "kind": tenant.kind.value,
        "role": role,
        "is_membership": True,
    }


# ---------------------------------------------------------------------------
# list_tenants
# ---------------------------------------------------------------------------


class ListTenantsInput(BaseModel):
    include_archived: bool = False
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None


class ListTenantsOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int
    next_cursor: str | None = None


async def list_tenants(ctx: MCPContext, req: ListTenantsInput) -> ListTenantsOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["tenant:read"])
    stmt = select(Tenant).where(
        or_(Tenant.id == ctx.tenant_id, Tenant.parent_tenant_id == ctx.tenant_id)
    )
    if not req.include_archived:
        stmt = stmt.where(Tenant.deprecated_at.is_(None))
    if req.cursor:
        stmt = stmt.where(Tenant.slug > req.cursor)
    rows = (
        (await ctx.db.execute(stmt.order_by(Tenant.slug).limit(req.limit + 1)))
        .scalars()
        .all()
    )
    home = rows[: req.limit]
    home_ids = {t.id for t in home}
    items = [_tenant_summary(tenant) for tenant in home]
    next_cursor = home[-1].slug if len(rows) > req.limit and home else None

    # Phase 4.3: append the caller's OTHER active-membership workspaces (label-only,
    # §2.4) so the switcher can list them. First page only (no cursor); a user
    # belongs to few workspaces. tenants_select RLS (0035) already permits these
    # rows; the explicit user_uc_uid filter scopes to the caller regardless of RLS.
    if not req.cursor:
        uc_uid = str(getattr(ctx, "user_id", "") or "")
        if uc_uid:
            mem_rows = (
                await ctx.db.execute(
                    text(
                        "SELECT tenant_id, role FROM user_tenant_membership "
                        "WHERE user_uc_uid = :u AND status = 'active'"
                    ),
                    {"u": uc_uid},
                )
            ).all()
            mem_roles = {row[0]: row[1] for row in mem_rows}
            extra_ids = [tid for tid in mem_roles if tid not in home_ids]
            if extra_ids:
                extra = (
                    await ctx.db.execute(
                        select(Tenant).where(Tenant.id.in_(extra_ids))
                    )
                ).scalars().all()
                for tenant in extra:
                    if req.include_archived or tenant.deprecated_at is None:
                        items.append(
                            _tenant_label_only(tenant, mem_roles.get(tenant.id))
                        )

    return ListTenantsOutput(
        items=items,
        count=len(items),
        next_cursor=next_cursor,
    )


# ---------------------------------------------------------------------------
# create_tenant
# ---------------------------------------------------------------------------


class CreateTenantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=2, max_length=60)
    display_name: str = Field(min_length=1, max_length=200)
    kind: Literal[TENANT_KINDS] = "brand"  # type: ignore[valid-type]
    owner_user_id: uuid.UUID
    parent_tenant_id: uuid.UUID | None = None
    hipaa_mode: bool = False
    gdpr_mode: bool = False
    data_residency: str | None = Field(default=None, max_length=40)
    carddav_enabled: bool = False
    branding: dict[str, Any] | None = None
    retention_policy: dict[str, Any] | None = None
    graph_mode: Literal[GRAPH_MODES] = "per_org_graph"  # type: ignore[valid-type]
    idempotency_key: uuid.UUID | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, slug: str) -> str:
        if not SLUG_RE.match(slug):
            raise ValueError(
                "slug must be lowercase, start with a letter, contain only "
                "a-z, 0-9, hyphen or underscore, 2-63 chars"
            )
        return slug


class CreateTenantOutput(ToolOutput):
    tenant_id: uuid.UUID
    slug: str
    graph_name: str
    qdrant_namespace: str
    garage_bucket_prefix: str
    status: str
    event_id: uuid.UUID | None = None


async def create_tenant(ctx: MCPContext, req: CreateTenantInput) -> CreateTenantOutput:
    require_role(ctx, "ADMIN")
    require_scopes(ctx, ["tenant:write"])

    cached = await check_or_register(
        ctx,
        idempotency_key=str(req.idempotency_key) if req.idempotency_key else None,
        tool_name="create_tenant",
        stable_args_hash=stable_hash(req),
    )
    if cached is not None:
        return CreateTenantOutput.model_validate(cached)

    # natural-key dedupe on slug
    existing = await ctx.db.scalar(select(Tenant).where(Tenant.slug == req.slug))
    if existing is not None:
        raise ToolError(SLUG_TAKEN, f"tenant slug already exists: {req.slug}")

    graph_name = f"contact_ops__{req.slug}"
    qdrant_namespace = f"contact_ops__{req.slug}"
    garage_bucket_prefix = f"contact-ops-{req.slug}"

    tenant = Tenant(
        slug=req.slug,
        kind=TenantKind(req.kind),
        display_name=req.display_name,
        owner_user_id=req.owner_user_id,
        parent_tenant_id=req.parent_tenant_id,
        branding=req.branding or {},
        retention_policy=req.retention_policy or {},
        hipaa_mode=req.hipaa_mode,
        gdpr_mode=req.gdpr_mode,
        data_residency=req.data_residency or "us-east",
        carddav_enabled=req.carddav_enabled,
        graph_mode=req.graph_mode,
        graph_name=graph_name,
        qdrant_namespace=qdrant_namespace,
        garage_bucket_prefix=garage_bucket_prefix,
    )
    ctx.db.add(tenant)
    await ctx.db.flush()

    event_id = await emit_action_event(
        ctx,
        event_type="tenant.create",
        aggregate_type="tenant",
        aggregate_id=tenant.id,
        payload_before=None,
        payload_after=_tenant_summary(tenant),
        confidence=req.confidence,
    )

    # Register the tenant with the billing substrate (Lago) at provisioning so
    # its later metered usage events do not 422 on an unknown customer. Best
    # effort + DORMANT by default (the manual/unconfigured provider no-ops); a
    # billing hiccup must never fail tenant creation. New tenants default to the
    # 'free' tier via the tenants.plan_tier server_default.
    await provision_lago_customer(
        tenant_id=tenant.id,
        plan_tier="free",
        settings=get_settings(),
    )

    result = CreateTenantOutput(
        tenant_id=tenant.id,
        slug=tenant.slug,
        graph_name=graph_name,
        qdrant_namespace=qdrant_namespace,
        garage_bucket_prefix=garage_bucket_prefix,
        status="applied",
        event_id=event_id,
    )
    if req.idempotency_key:
        await store_result(
            ctx, str(req.idempotency_key), result.model_dump(mode="json")
        )
    return result


# ---------------------------------------------------------------------------
# get_tenant
# ---------------------------------------------------------------------------


class GetTenantInput(BaseModel):
    tenant_id: uuid.UUID


class GetTenantOutput(ToolOutput):
    tenant: dict[str, Any]
    person_count: int
    organization_count: int


async def get_tenant(ctx: MCPContext, req: GetTenantInput) -> GetTenantOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["tenant:read"])
    _require_target_tenant(ctx, req.tenant_id)
    tenant = await _load_tenant(ctx, req.tenant_id)

    person_count = await ctx.db.scalar(
        select(func.count())
        .select_from(Person)
        .where(Person.canonical_owner_tenant_id == tenant.id)
    )
    org_count = await ctx.db.scalar(
        select(func.count())
        .select_from(Organization)
        .where(Organization.canonical_owner_tenant_id == tenant.id)
    )
    return GetTenantOutput(
        tenant=_tenant_summary(tenant),
        person_count=int(person_count or 0),
        organization_count=int(org_count or 0),
    )


# ---------------------------------------------------------------------------
# update_tenant_settings
# ---------------------------------------------------------------------------


class UpdateTenantSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    etag: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    branding: dict[str, Any] | None = None
    retention_policy: dict[str, Any] | None = None
    hipaa_mode: bool | None = None
    gdpr_mode: bool | None = None
    carddav_enabled: bool | None = None
    data_intel_publish_consent: bool | None = None
    data_residency: str | None = Field(default=None, max_length=40)
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdateTenantSettingsOutput(ToolOutput):
    tenant_id: uuid.UUID
    etag: str
    changed_fields: list[str]
    status: str
    event_id: uuid.UUID | None = None


async def update_tenant_settings(
    ctx: MCPContext, req: UpdateTenantSettingsInput
) -> UpdateTenantSettingsOutput:
    require_role(ctx, "ADMIN")
    require_scopes(ctx, ["tenant:write"])
    _require_target_tenant(ctx, req.tenant_id)

    tenant = await _load_tenant(ctx, req.tenant_id)
    if tenant.etag != req.etag:
        raise ToolError(
            VALIDATION_FAILED,
            "stale tenant etag",
            hint="reload tenant via get_tenant before patching",
        )
    if req.hipaa_mode is False and tenant.hipaa_mode is True:
        raise ToolError(
            HIPAA_MODE_CANNOT_DISABLE,
            "HIPAA mode cannot be disabled once enabled",
            hint="contact platform support",
        )

    before = _tenant_summary(tenant)
    patch = req.model_dump(
        exclude={"tenant_id", "etag", "confidence"}, exclude_unset=True
    )
    changed: list[str] = []
    for field, value in patch.items():
        if getattr(tenant, field) != value:
            setattr(tenant, field, value)
            changed.append(field)

    if not changed:
        return UpdateTenantSettingsOutput(
            tenant_id=tenant.id,
            etag=tenant.etag,
            changed_fields=[],
            status="applied",
        )
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="tenant.update",
        aggregate_type="tenant",
        aggregate_id=tenant.id,
        payload_before=before,
        payload_after=_tenant_summary(tenant),
        confidence=req.confidence,
    )
    return UpdateTenantSettingsOutput(
        tenant_id=tenant.id,
        etag=tenant.etag,
        changed_fields=changed,
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# set_tenant_membership (person)
# ---------------------------------------------------------------------------


class SetTenantMembershipInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID
    tenant_id: uuid.UUID
    visibility: Literal[MEMBERSHIP_VISIBILITIES] = "visible"  # type: ignore[valid-type]
    notes: str | None = Field(default=None, max_length=8000)
    tags: list[str] | None = Field(default=None, max_length=50)
    custom_attrs: dict[str, Any] | None = None
    consent_basis: Literal[CONSENT_BASES] | None = None  # type: ignore[valid-type]
    confidence: float = Field(default=1.0, ge=0, le=1)


class SetTenantMembershipOutput(ToolOutput):
    person_id: uuid.UUID
    tenant_id: uuid.UUID
    visibility: str
    status: str
    event_id: uuid.UUID | None = None


async def set_tenant_membership(
    ctx: MCPContext, req: SetTenantMembershipInput
) -> SetTenantMembershipOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["tenant:write", "membership:write"])
    _require_target_tenant(ctx, req.tenant_id)

    tenant = await _load_tenant(ctx, req.tenant_id)
    if tenant.hipaa_mode and req.consent_basis is None:
        raise ToolError(
            HIPAA_TENANT_REQUIRES_CONSENT_BASIS,
            "consent_basis is required when target tenant has hipaa_mode",
        )

    person = await ctx.db.get(Person, req.person_id)
    if person is None:
        raise ToolError(PERSON_NOT_FOUND, f"person {req.person_id} not found")

    membership = await ctx.db.get(
        PersonTenantMembership, (req.person_id, req.tenant_id)
    )
    payload_before: dict[str, Any] | None
    if membership is None:
        membership = PersonTenantMembership(
            person_id=req.person_id,
            tenant_id=req.tenant_id,
            visibility=req.visibility,
            notes=req.notes,
            tags=list(req.tags or []),
            custom_attrs=req.custom_attrs or {},
            consent_basis=(
                ConsentBasis(req.consent_basis) if req.consent_basis else None
            ),
        )
        ctx.db.add(membership)
        payload_before = None
    else:
        payload_before = _membership_summary(membership)
        membership.visibility = req.visibility
        if req.notes is not None:
            membership.notes = req.notes
        if req.tags is not None:
            membership.tags = list(req.tags)
        if req.custom_attrs is not None:
            membership.custom_attrs = req.custom_attrs
        if req.consent_basis is not None:
            membership.consent_basis = ConsentBasis(req.consent_basis)
    await ctx.db.flush()

    event_id = await emit_action_event(
        ctx,
        event_type="tenant_membership.set",
        aggregate_type="person",
        aggregate_id=req.person_id,
        affected_ids=[req.person_id],
        payload_before=payload_before,
        payload_after=_membership_summary(membership),
        confidence=req.confidence,
    )
    return SetTenantMembershipOutput(
        person_id=req.person_id,
        tenant_id=req.tenant_id,
        visibility=membership.visibility,
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# grant_workspace_access (user_tenant_membership — the workspace ACCESS primitive)
# ---------------------------------------------------------------------------


WORKSPACE_ROLES = ("viewer", "member", "manager", "admin")


class GrantWorkspaceAccessInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_uc_uid: str = Field(min_length=1, max_length=255)
    role: Literal[WORKSPACE_ROLES] = "member"  # type: ignore[valid-type]
    is_default: bool = False
    consent_basis: Literal[CONSENT_BASES] | None = None  # type: ignore[valid-type]
    confidence: float = Field(default=1.0, ge=0, le=1)


class GrantWorkspaceAccessOutput(ToolOutput):
    user_uc_uid: str
    tenant_id: uuid.UUID
    role: str
    status: str
    event_id: uuid.UUID | None = None


async def grant_workspace_access(
    ctx: MCPContext, req: GrantWorkspaceAccessInput
) -> GrantWorkspaceAccessOutput:
    """Grant a user an active membership in the CALLER'S workspace (ctx.tenant_id).

    The user->tenant ACCESS primitive behind the workspace switcher (distinct from
    set_tenant_membership, which is a contact-visibility lens). RLS WITH CHECK on
    user_tenant_membership enforces tenant_id = current_tenant_id(), so an admin can
    only grant into the workspace their session is bound to. HIPAA workspaces require
    a consent_basis (mirrors set_tenant_membership / design §3.6).
    """
    require_role(ctx, "ADMIN")
    require_scopes(ctx, ["tenant:write", "membership:write"])

    tenant = await _load_tenant(ctx, ctx.tenant_id)
    if tenant.hipaa_mode and req.consent_basis is None:
        raise ToolError(
            HIPAA_TENANT_REQUIRES_CONSENT_BASIS,
            "consent_basis is required when the workspace has hipaa_mode",
        )

    await ctx.db.execute(
        text(
            """
            INSERT INTO user_tenant_membership
                (user_uc_uid, tenant_id, role, status, is_default, consent_basis, added_by)
            VALUES (:u, :t, :r, 'active', :d, CAST(:cb AS consent_basis), :by)
            ON CONFLICT (user_uc_uid, tenant_id) DO UPDATE
              SET role = EXCLUDED.role,
                  status = 'active',
                  is_default = EXCLUDED.is_default,
                  consent_basis = EXCLUDED.consent_basis,
                  updated_at = now()
            """
        ),
        {
            "u": req.user_uc_uid,
            "t": str(ctx.tenant_id),
            "r": req.role,
            "d": req.is_default,
            "cb": req.consent_basis,
            "by": str(ctx.user_id),
        },
    )
    await ctx.db.flush()

    event_id = await emit_action_event(
        ctx,
        event_type="workspace_access.grant",
        aggregate_type="tenant",
        aggregate_id=ctx.tenant_id,
        payload_before=None,
        payload_after={
            "user_uc_uid": req.user_uc_uid,
            "tenant_id": str(ctx.tenant_id),
            "role": req.role,
            "is_default": req.is_default,
        },
        confidence=req.confidence,
    )
    return GrantWorkspaceAccessOutput(
        user_uc_uid=req.user_uc_uid,
        tenant_id=ctx.tenant_id,
        role=req.role,
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# Workspace members (user_tenant_membership management — the Team surface)
# ---------------------------------------------------------------------------
#
# These four tools are the human-facing team management surface (the Settings →
# Members UI) over the same user_tenant_membership table grant_workspace_access
# writes. They differ from grant_workspace_access in two deliberate ways:
#   1. Authority is the caller's WORKSPACE membership role (an active 'admin' row
#      for the current workspace), not the Keycloak realm ADMIN role — a self-serve
#      owner has membership role='admin' but only the default realm role, so realm
#      gating would lock every owner out of their own team. A realm ADMIN is also
#      allowed (platform/operator override).
#   2. invite_workspace_member resolves an *email* to a Keycloak sub (via the
#      contact-ops-switch service account) so an admin invites by email, never by
#      an opaque uc_uid. A not-yet-registered email is rejected with a clear,
#      actionable error (pending email-invitations for brand-new users land with
#      the [Email]/[Signup] milestones).
# All four run under utm_tenant_scope RLS (tenant_id = current_tenant_id()), so
# they can only ever read/write the caller's CURRENT workspace.

USER_NOT_REGISTERED = "USER_NOT_REGISTERED"
MEMBER_NOT_FOUND = "MEMBER_NOT_FOUND"
LAST_ADMIN = "LAST_ADMIN"
CANNOT_TARGET_SELF = "CANNOT_TARGET_SELF"
DIRECTORY_UNAVAILABLE = "DIRECTORY_UNAVAILABLE"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def _require_workspace_admin(ctx: MCPContext) -> None:
    """Authorize a member-management action on the caller's CURRENT workspace.

    Authority is the caller's workspace membership role (active 'admin' for
    ctx.tenant_id), with a Keycloak realm ADMIN allowed as a platform override.
    Runs under utm_tenant_scope RLS so it only ever sees the current workspace.
    """
    if get_role(ctx.claims) == "ADMIN":
        return
    row = await ctx.db.execute(
        text(
            """
            SELECT 1 FROM user_tenant_membership
            WHERE user_uc_uid = :u AND tenant_id = :t
              AND role = 'admin' AND status = 'active'
            """
        ),
        {"u": str(ctx.user_id), "t": str(ctx.tenant_id)},
    )
    if row.first() is None:
        raise ToolError(
            INSUFFICIENT_ROLE,
            "workspace admin role required to manage members",
            retryable=False,
        )


async def _count_active_admins(ctx: MCPContext) -> int:
    row = await ctx.db.execute(
        text(
            "SELECT count(*) FROM user_tenant_membership "
            "WHERE tenant_id = :t AND role = 'admin' AND status = 'active'"
        ),
        {"t": str(ctx.tenant_id)},
    )
    return int(row.scalar() or 0)


async def _load_member(
    ctx: MCPContext, user_uc_uid: str
) -> dict[str, Any] | None:
    row = await ctx.db.execute(
        text(
            "SELECT user_uc_uid, role, status FROM user_tenant_membership "
            "WHERE user_uc_uid = :u AND tenant_id = :t"
        ),
        {"u": user_uc_uid, "t": str(ctx.tenant_id)},
    )
    return row.mappings().first()


class WorkspaceMember(BaseModel):
    user_uc_uid: str
    role: str
    status: str
    is_default: bool
    added_at: datetime | None = None
    updated_at: datetime | None = None
    added_by: str | None = None
    # Directory-enriched label fields (best-effort via Keycloak; null if the
    # directory is unconfigured or the user can't be read back).
    email: str | None = None
    username: str | None = None
    full_name: str | None = None
    is_self: bool = False


class ListWorkspaceMembersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_inactive: bool = False


class ListWorkspaceMembersOutput(ToolOutput):
    items: list[WorkspaceMember]
    count: int
    tenant_id: uuid.UUID
    directory_enriched: bool


async def list_workspace_members(
    ctx: MCPContext, req: ListWorkspaceMembersInput
) -> ListWorkspaceMembersOutput:
    """List the members of the caller's current workspace (admin-only)."""
    require_scopes(ctx, ["tenant:read"])
    await _require_workspace_admin(ctx)

    where_status = "" if req.include_inactive else "AND status = 'active'"
    rows = (
        await ctx.db.execute(
            text(
                f"""
                SELECT user_uc_uid, role, status, is_default,
                       added_at, updated_at, added_by
                FROM user_tenant_membership
                WHERE tenant_id = :t {where_status}
                ORDER BY (role = 'admin') DESC, added_at ASC
                """
            ),
            {"t": str(ctx.tenant_id)},
        )
    ).mappings().all()

    settings = get_settings()
    directory = await keycloak_admin.get_users_by_ids(
        settings, [str(r["user_uc_uid"]) for r in rows]
    )
    enriched = bool(directory)

    items: list[WorkspaceMember] = []
    for r in rows:
        uid = str(r["user_uc_uid"])
        label = directory.get(uid, {})
        items.append(
            WorkspaceMember(
                user_uc_uid=uid,
                role=r["role"],
                status=r["status"],
                is_default=r["is_default"],
                added_at=r["added_at"],
                updated_at=r["updated_at"],
                added_by=r["added_by"],
                email=label.get("email"),
                username=label.get("username"),
                full_name=label.get("full_name"),
                is_self=(uid == str(ctx.user_id)),
            )
        )
    return ListWorkspaceMembersOutput(
        items=items,
        count=len(items),
        tenant_id=ctx.tenant_id,
        directory_enriched=enriched,
    )


class InviteWorkspaceMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    role: Literal[WORKSPACE_ROLES] = "member"  # type: ignore[valid-type]
    is_default: bool = False
    consent_basis: Literal[CONSENT_BASES] | None = None  # type: ignore[valid-type]
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip()
        if not _EMAIL_RE.match(value):
            raise ValueError("invalid email address")
        return value


class InviteWorkspaceMemberOutput(ToolOutput):
    user_uc_uid: str
    email: str
    role: str
    status: str
    created: bool
    event_id: uuid.UUID | None = None


async def invite_workspace_member(
    ctx: MCPContext, req: InviteWorkspaceMemberInput
) -> InviteWorkspaceMemberOutput:
    """Invite a teammate by email into the caller's workspace (admin-only).

    Resolves the email to an existing Keycloak account and grants an active
    membership. A not-yet-registered email is rejected with USER_NOT_REGISTERED
    (the teammate must sign in once to create their account, then be re-invited).
    Idempotent on (user, workspace): re-inviting updates the role.
    """
    require_scopes(ctx, ["tenant:write", "membership:write"])
    await _require_workspace_admin(ctx)

    tenant = await _load_tenant(ctx, ctx.tenant_id)
    if tenant.hipaa_mode and req.consent_basis is None:
        raise ToolError(
            HIPAA_TENANT_REQUIRES_CONSENT_BASIS,
            "consent_basis is required when the workspace has hipaa_mode",
        )

    settings = get_settings()
    if not keycloak_admin.is_configured(settings):
        raise ToolError(
            DIRECTORY_UNAVAILABLE,
            "member directory is not configured on this deployment",
            hint="set KEYCLOAK_SWITCH_CLIENT_SECRET to enable email invites",
            retryable=False,
        )

    user = await keycloak_admin.find_user_by_email(settings, req.email)
    if user is None or not user.get("uc_uid"):
        raise ToolError(
            USER_NOT_REGISTERED,
            f"no Unicorn Commander account found for {req.email}",
            hint=(
                "ask them to sign in once at the app to create their account, "
                "then invite again"
            ),
            retryable=False,
        )
    user_uc_uid = str(user["uc_uid"])

    existing = await _load_member(ctx, user_uc_uid)
    created = existing is None or existing["status"] != "active"

    await ctx.db.execute(
        text(
            """
            INSERT INTO user_tenant_membership
                (user_uc_uid, tenant_id, role, status, is_default, consent_basis, added_by)
            VALUES (:u, :t, :r, 'active', :d, CAST(:cb AS consent_basis), :by)
            ON CONFLICT (user_uc_uid, tenant_id) DO UPDATE
              SET role = EXCLUDED.role,
                  status = 'active',
                  is_default = EXCLUDED.is_default,
                  consent_basis = EXCLUDED.consent_basis,
                  updated_at = now()
            """
        ),
        {
            "u": user_uc_uid,
            "t": str(ctx.tenant_id),
            "r": req.role,
            "d": req.is_default,
            "cb": req.consent_basis,
            "by": str(ctx.user_id),
        },
    )
    await ctx.db.flush()

    event_id = await emit_action_event(
        ctx,
        event_type="workspace_access.grant",
        aggregate_type="tenant",
        aggregate_id=ctx.tenant_id,
        payload_before=None,
        payload_after={
            "user_uc_uid": user_uc_uid,
            "email": req.email,
            "tenant_id": str(ctx.tenant_id),
            "role": req.role,
            "is_default": req.is_default,
            "via": "invite_by_email",
        },
        confidence=req.confidence,
    )

    # Best-effort invite email AFTER the grant is durable. Dormant (Noop logs
    # `email_would_send`) until Postmark is configured + EMAIL_SENDING_ENABLED.
    # send_member_invite never raises — it swallows any failure to a warning — so
    # a flaky/unconfigured mailer can never undo the membership grant.
    from contact_ops.services import notifications

    await notifications.send_member_invite(
        to_email=req.email,
        workspace_name=tenant.display_name,
        role=req.role,
        inviter=str(ctx.user_id),
        settings=settings,
    )

    return InviteWorkspaceMemberOutput(
        user_uc_uid=user_uc_uid,
        email=req.email,
        role=req.role,
        status="active",
        created=created,
        event_id=event_id,
    )


class UpdateMemberRoleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_uc_uid: str = Field(min_length=1, max_length=255)
    role: Literal[WORKSPACE_ROLES] = "member"  # type: ignore[valid-type]
    confidence: float = Field(default=1.0, ge=0, le=1)


class UpdateMemberRoleOutput(ToolOutput):
    user_uc_uid: str
    role: str
    status: str
    event_id: uuid.UUID | None = None


async def update_workspace_member_role(
    ctx: MCPContext, req: UpdateMemberRoleInput
) -> UpdateMemberRoleOutput:
    """Change a member's role in the caller's workspace (admin-only).

    Refuses to demote the last active admin (anti-lockout).
    """
    require_scopes(ctx, ["tenant:write", "membership:write"])
    await _require_workspace_admin(ctx)

    member = await _load_member(ctx, req.user_uc_uid)
    if member is None or member["status"] != "active":
        raise ToolError(MEMBER_NOT_FOUND, "active member not found in this workspace")

    demoting_admin = member["role"] == "admin" and req.role != "admin"
    if demoting_admin and await _count_active_admins(ctx) <= 1:
        raise ToolError(
            LAST_ADMIN,
            "cannot demote the last admin of the workspace",
            hint="promote another member to admin first",
            retryable=False,
        )

    if member["role"] == req.role:
        return UpdateMemberRoleOutput(
            user_uc_uid=req.user_uc_uid, role=req.role, status="active"
        )

    await ctx.db.execute(
        text(
            "UPDATE user_tenant_membership SET role = :r, updated_at = now() "
            "WHERE user_uc_uid = :u AND tenant_id = :t"
        ),
        {"r": req.role, "u": req.user_uc_uid, "t": str(ctx.tenant_id)},
    )
    await ctx.db.flush()

    event_id = await emit_action_event(
        ctx,
        event_type="workspace_access.role_change",
        aggregate_type="tenant",
        aggregate_id=ctx.tenant_id,
        payload_before={"user_uc_uid": req.user_uc_uid, "role": member["role"]},
        payload_after={"user_uc_uid": req.user_uc_uid, "role": req.role},
        confidence=req.confidence,
    )
    return UpdateMemberRoleOutput(
        user_uc_uid=req.user_uc_uid,
        role=req.role,
        status="active",
        event_id=event_id,
    )


class RevokeWorkspaceMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_uc_uid: str = Field(min_length=1, max_length=255)
    confidence: float = Field(default=1.0, ge=0, le=1)


class RevokeWorkspaceMemberOutput(ToolOutput):
    user_uc_uid: str
    status: str
    event_id: uuid.UUID | None = None


async def revoke_workspace_member(
    ctx: MCPContext, req: RevokeWorkspaceMemberInput
) -> RevokeWorkspaceMemberOutput:
    """Revoke a member's access to the caller's workspace (admin-only).

    With the membership gate enforced, revocation cuts the user's access (and
    their PATs/federated tokens for this workspace) immediately. Refuses to
    revoke yourself or the last active admin (anti-lockout).
    """
    require_scopes(ctx, ["tenant:write", "membership:write"])
    await _require_workspace_admin(ctx)

    if str(req.user_uc_uid) == str(ctx.user_id):
        raise ToolError(
            CANNOT_TARGET_SELF,
            "you cannot revoke your own access",
            hint="ask another workspace admin to remove you",
            retryable=False,
        )

    member = await _load_member(ctx, req.user_uc_uid)
    if member is None or member["status"] != "active":
        raise ToolError(MEMBER_NOT_FOUND, "active member not found in this workspace")

    if member["role"] == "admin" and await _count_active_admins(ctx) <= 1:
        raise ToolError(
            LAST_ADMIN,
            "cannot revoke the last admin of the workspace",
            retryable=False,
        )

    await ctx.db.execute(
        text(
            "UPDATE user_tenant_membership SET status = 'revoked', updated_at = now() "
            "WHERE user_uc_uid = :u AND tenant_id = :t"
        ),
        {"u": req.user_uc_uid, "t": str(ctx.tenant_id)},
    )
    await ctx.db.flush()

    event_id = await emit_action_event(
        ctx,
        event_type="workspace_access.revoke",
        aggregate_type="tenant",
        aggregate_id=ctx.tenant_id,
        payload_before={"user_uc_uid": req.user_uc_uid, "role": member["role"]},
        payload_after={"user_uc_uid": req.user_uc_uid, "status": "revoked"},
        confidence=req.confidence,
    )
    return RevokeWorkspaceMemberOutput(
        user_uc_uid=req.user_uc_uid,
        status="revoked",
        event_id=event_id,
    )


def _membership_summary(membership: PersonTenantMembership) -> dict[str, Any]:
    return {
        "person_id": str(membership.person_id),
        "tenant_id": str(membership.tenant_id),
        "visibility": membership.visibility,
        "notes": membership.notes,
        "tags": list(membership.tags or []),
        "custom_attrs": dict(membership.custom_attrs or {}),
        "consent_basis": (
            membership.consent_basis.value if membership.consent_basis else None
        ),
    }


def _org_membership_summary(
    membership: OrganizationTenantMembership,
) -> dict[str, Any]:
    return {
        "organization_id": str(membership.organization_id),
        "tenant_id": str(membership.tenant_id),
        "visibility": membership.visibility,
        "notes": membership.notes,
        "tags": list(membership.tags or []),
        "custom_attrs": dict(membership.custom_attrs or {}),
    }


# ---------------------------------------------------------------------------
# end_tenant_membership
# ---------------------------------------------------------------------------


END_REASONS = ("no_longer_relevant", "gdpr_request", "tenant_cleanup", "other")


class EndTenantMembershipInput(BaseModel):
    person_id: uuid.UUID
    tenant_id: uuid.UUID
    reason: Literal[END_REASONS] = "no_longer_relevant"  # type: ignore[valid-type]
    confidence: float = Field(default=1.0, ge=0, le=1)


class EndTenantMembershipOutput(ToolOutput):
    person_id: uuid.UUID
    tenant_id: uuid.UUID
    ended_at: datetime
    status: str
    event_id: uuid.UUID | None = None


async def end_tenant_membership(
    ctx: MCPContext, req: EndTenantMembershipInput
) -> EndTenantMembershipOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["membership:write"])
    _require_target_tenant(ctx, req.tenant_id)

    membership = await ctx.db.get(
        PersonTenantMembership, (req.person_id, req.tenant_id)
    )
    if membership is None:
        raise ToolError(MEMBERSHIP_NOT_FOUND, "membership not found")
    before = _membership_summary(membership)
    membership.visibility = "archived"
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="tenant_membership.end",
        aggregate_type="person",
        aggregate_id=req.person_id,
        affected_ids=[req.person_id],
        payload_before=before,
        payload_after={**_membership_summary(membership), "reason": req.reason},
        confidence=req.confidence,
    )
    return EndTenantMembershipOutput(
        person_id=req.person_id,
        tenant_id=req.tenant_id,
        ended_at=datetime.now().astimezone(),
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# set_organization_tenant_membership
# ---------------------------------------------------------------------------


class SetOrgTenantMembershipInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: uuid.UUID
    tenant_id: uuid.UUID
    visibility: Literal[MEMBERSHIP_VISIBILITIES] = "visible"  # type: ignore[valid-type]
    notes: str | None = Field(default=None, max_length=8000)
    tags: list[str] | None = Field(default=None, max_length=50)
    custom_attrs: dict[str, Any] | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class SetOrgTenantMembershipOutput(ToolOutput):
    organization_id: uuid.UUID
    tenant_id: uuid.UUID
    visibility: str
    status: str
    event_id: uuid.UUID | None = None


async def set_organization_tenant_membership(
    ctx: MCPContext, req: SetOrgTenantMembershipInput
) -> SetOrgTenantMembershipOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["tenant:write", "membership:write"])
    _require_target_tenant(ctx, req.tenant_id)

    org = await ctx.db.get(Organization, req.organization_id)
    if org is None:
        raise ToolError(ORG_NOT_FOUND, f"organization {req.organization_id} not found")
    await _load_tenant(ctx, req.tenant_id)

    membership = await ctx.db.get(
        OrganizationTenantMembership, (req.organization_id, req.tenant_id)
    )
    payload_before: dict[str, Any] | None
    if membership is None:
        membership = OrganizationTenantMembership(
            organization_id=req.organization_id,
            tenant_id=req.tenant_id,
            visibility=req.visibility,
            notes=req.notes,
            tags=list(req.tags or []),
            custom_attrs=req.custom_attrs or {},
        )
        ctx.db.add(membership)
        payload_before = None
    else:
        payload_before = _org_membership_summary(membership)
        membership.visibility = req.visibility
        if req.notes is not None:
            membership.notes = req.notes
        if req.tags is not None:
            membership.tags = list(req.tags)
        if req.custom_attrs is not None:
            membership.custom_attrs = req.custom_attrs
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="org_tenant_membership.set",
        aggregate_type="organization",
        aggregate_id=req.organization_id,
        affected_ids=[req.organization_id],
        payload_before=payload_before,
        payload_after=_org_membership_summary(membership),
        confidence=req.confidence,
    )
    return SetOrgTenantMembershipOutput(
        organization_id=req.organization_id,
        tenant_id=req.tenant_id,
        visibility=membership.visibility,
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# offboard_tenant (tenant lifecycle — DESTRUCTIVE, ADMIN-only)
# ---------------------------------------------------------------------------

OFFBOARD_TENANT_PHRASE = "OFFBOARD TENANT PERMANENTLY"


async def _purge_tenant_external_best_effort(
    *, slug: str, graph_name: str | None
) -> dict[str, Any]:
    """Purge the tenant's WHOLE external data planes; swallow every error.

    Deletes the tenant's per-tenant Garage buckets and drops its FalkorDB graph
    outright (not per-node). A missing / unhealthy / operator-owned external
    store must never fail an offboard — the operational rows are already gone
    from Postgres, the legal source of truth. Each backend is lazy-imported so
    this module carries no import-time dependency on them (mirrors the
    best-effort pattern in ``compliance_erasure``).
    """
    out: dict[str, Any] = {"garage": "skipped", "falkordb": "skipped"}

    # --- Garage: delete every per-tenant bucket ------------------------------
    try:
        from contact_ops.services.garage_lifecycle import delete_tenant_buckets

        out["garage"] = await delete_tenant_buckets(tenant_slug=slug)
    except Exception as exc:  # noqa: BLE001
        logger.warning("offboard.garage_failed", slug=slug, error=str(exc))
        out["garage"] = {"error": str(exc)}

    # --- FalkorDB: drop the whole tenant graph -------------------------------
    if graph_name:
        try:
            from contact_ops.agents.graph_sync.falkordb_client import (
                FalkorDBGraphClient,
            )

            client = FalkorDBGraphClient()
            try:
                deleted = await client.delete_graph(graph_name)
                out["falkordb"] = {"graph_deleted": bool(deleted)}
            finally:
                await client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "offboard.falkordb_failed", graph=graph_name, error=str(exc)
            )
            out["falkordb"] = {"error": str(exc)}

    return out


class OffboardTenantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_phrase: Literal["OFFBOARD TENANT PERMANENTLY"]
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: uuid.UUID | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class OffboardTenantOutput(ToolOutput):
    tenant_id: uuid.UUID
    slug: str
    persons_purged: int
    ledger_anonymized: int
    memberships_revoked: int
    external: dict[str, Any]
    already_offboarded: bool
    status: str
    event_id: uuid.UUID | None = None


async def offboard_tenant(
    ctx: MCPContext, req: OffboardTenantInput
) -> OffboardTenantOutput:
    """Offboard the caller's CURRENT tenant: purge rows + external planes.

    Operates on ``ctx.tenant_id`` (the RLS-bound workspace), NOT an arbitrary
    target — the runtime role is NOBYPASSRLS, so a cross-tenant offboard would
    silently see nothing. To offboard a different tenant, platform binds their
    session to it (the established switch pattern), exactly like
    ``erase_tenant_account`` / the workspace-member tools.

    Steps (each safe to re-run):
      1. purge every person + anonymize the hash-chained ledger in place
         (delegates to ``compliance_erasure.erase_tenant_account``);
      2. revoke every active workspace membership for the tenant;
      3. best-effort delete the tenant's Garage buckets + drop its FalkorDB
         graph (never fails the offboard);
      4. stamp the tenant deprecated + an ``offboarded_at`` retention marker;
      5. append a ``tenant.offboard`` action_event.

    Idempotent two ways: an in-memory idempotency_key replays the prior result,
    and a durable ``offboarded_at`` marker short-circuits a re-run to a no-op.
    Guarded by the literal phrase ``OFFBOARD TENANT PERMANENTLY``. ADMIN only.
    """
    require_role(ctx, "ADMIN")
    require_scopes(ctx, ["tenant:write", "person:delete"])

    if req.confirmation_phrase != OFFBOARD_TENANT_PHRASE:
        raise ToolError(
            CONFIRMATION_PHRASE_MISMATCH,
            "confirmation phrase mismatch",
            retryable=False,
            hint=f"send confirmation_phrase exactly: {OFFBOARD_TENANT_PHRASE!r}",
        )

    cached = await check_or_register(
        ctx,
        idempotency_key=str(req.idempotency_key) if req.idempotency_key else None,
        tool_name="offboard_tenant",
        stable_args_hash=stable_hash(req),
    )
    if cached is not None:
        return OffboardTenantOutput.model_validate(cached)

    tenant = await _load_tenant(ctx, ctx.tenant_id)
    slug = tenant.slug

    # Durable idempotency: an already-offboarded tenant short-circuits to a no-op
    # so a retry (or a second operator) can never double-purge or re-emit.
    marker = dict(tenant.retention_policy or {})
    if marker.get("offboarded_at"):
        result = OffboardTenantOutput(
            tenant_id=tenant.id,
            slug=slug,
            persons_purged=0,
            ledger_anonymized=0,
            memberships_revoked=0,
            external={"note": "tenant already offboarded"},
            already_offboarded=True,
            status="applied",
        )
        if req.idempotency_key:
            await store_result(
                ctx, str(req.idempotency_key), result.model_dump(mode="json")
            )
        return result

    # (1) Purge persons + anonymize the ledger in place (RLS-scoped to ctx tenant).
    erase = await compliance_erasure.erase_tenant_account(ctx.db, str(ctx.tenant_id))

    # (2) Revoke every active workspace membership (utm_tenant_scope RLS confines
    #     this to the current tenant regardless of the WHERE clause).
    rev = await ctx.db.execute(
        text(
            """
            UPDATE user_tenant_membership
            SET status = 'revoked', updated_at = now()
            WHERE tenant_id = :t AND status = 'active'
            """
        ),
        {"t": str(ctx.tenant_id)},
    )
    memberships_revoked = rev.rowcount or 0

    # (3) Best-effort external teardown — whole buckets + whole graph.
    external = await _purge_tenant_external_best_effort(
        slug=slug, graph_name=tenant.graph_name
    )

    # (4) Stamp the tenant offboarded (deprecated + retention marker). Reassign a
    #     new dict so SQLAlchemy detects the JSONB mutation.
    now = datetime.now(UTC)
    marker.update(
        {
            "offboarded_at": now.isoformat(),
            "offboarded_by": str(ctx.user_id),
            "offboard_reason": req.reason,
        }
    )
    tenant.retention_policy = marker
    # tenants.deprecated_at is a naive TIMESTAMP column — strip tzinfo so asyncpg
    # doesn't reject a tz-aware value (the ISO marker above keeps the offset).
    tenant.deprecated_at = now.replace(tzinfo=None)
    await ctx.db.flush()

    persons_purged = int(erase.get("persons_purged", 0) or 0)
    ledger_anonymized = int(erase.get("ledger_anonymized", 0) or 0)

    # (5) Append the offboard action_event (non-PII summary; never redacted).
    event_id = await emit_action_event(
        ctx,
        event_type="tenant.offboard",
        aggregate_type="tenant",
        aggregate_id=tenant.id,
        payload_before=None,
        payload_after={
            "tenant_id": str(tenant.id),
            "slug": slug,
            "reason": req.reason,
            "persons_purged": persons_purged,
            "ledger_anonymized": ledger_anonymized,
            "memberships_revoked": memberships_revoked,
            "external": external,
            "offboarded_at": now.isoformat(),
        },
        confidence=req.confidence,
    )

    result = OffboardTenantOutput(
        tenant_id=tenant.id,
        slug=slug,
        persons_purged=persons_purged,
        ledger_anonymized=ledger_anonymized,
        memberships_revoked=memberships_revoked,
        external=external,
        already_offboarded=False,
        status="applied",
        event_id=event_id,
    )
    if req.idempotency_key:
        await store_result(
            ctx, str(req.idempotency_key), result.model_dump(mode="json")
        )
    return result


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


def register_tenancy_tools() -> None:
    register(
        name="list_tenants",
        description=(
            "List tenants the caller can see (own tenant plus child tenants). "
            "Requires CLIENT and tenant:read."
        ),
        input_model=ListTenantsInput,
        output_model=ListTenantsOutput,
        handler=list_tenants,
        required_role="CLIENT",
        required_scopes=("tenant:read",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="create_tenant",
        description=(
            "Create a new tenant. Platform ADMIN only. Provisions graph name, "
            "Qdrant namespace, and Garage bucket prefix per ecosystem convention."
        ),
        input_model=CreateTenantInput,
        output_model=CreateTenantOutput,
        handler=create_tenant,
        required_role="ADMIN",
        required_scopes=("tenant:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
    register(
        name="get_tenant",
        description=(
            "Return a single tenant record with person and organization counts. "
            "Requires CLIENT and tenant:read."
        ),
        input_model=GetTenantInput,
        output_model=GetTenantOutput,
        handler=get_tenant,
        required_role="CLIENT",
        required_scopes=("tenant:read",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="update_tenant_settings",
        description=(
            "Patch tenant settings (branding, retention, HIPAA/GDPR flags) "
            "with etag optimistic concurrency. Requires ADMIN and tenant:write."
        ),
        input_model=UpdateTenantSettingsInput,
        output_model=UpdateTenantSettingsOutput,
        handler=update_tenant_settings,
        required_role="ADMIN",
        required_scopes=("tenant:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="set_tenant_membership",
        description=(
            "Upsert a person's per-tenant membership (notes, tags, custom "
            "attrs, visibility, consent basis). Requires STAFF and "
            "tenant:write + membership:write."
        ),
        input_model=SetTenantMembershipInput,
        output_model=SetTenantMembershipOutput,
        handler=set_tenant_membership,
        required_role="STAFF",
        required_scopes=("tenant:write", "membership:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
    register(
        name="grant_workspace_access",
        description=(
            "Grant a user (by uc_uid) an active membership in the caller's "
            "workspace, the access primitive behind the workspace switcher. "
            "Requires ADMIN and tenant:write + membership:write."
        ),
        input_model=GrantWorkspaceAccessInput,
        output_model=GrantWorkspaceAccessOutput,
        handler=grant_workspace_access,
        required_role="ADMIN",
        required_scopes=("tenant:write", "membership:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
    register(
        name="list_workspace_members",
        description=(
            "List the members of the caller's current workspace (uc_uid, role, "
            "status, joined-at), directory-enriched with email/name. Workspace "
            "admin only. Requires tenant:read."
        ),
        input_model=ListWorkspaceMembersInput,
        output_model=ListWorkspaceMembersOutput,
        handler=list_workspace_members,
        required_role="CLIENT",
        required_scopes=("tenant:read",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="invite_workspace_member",
        description=(
            "Invite a teammate by EMAIL into the caller's workspace and grant "
            "them an active membership. Resolves the email to an existing "
            "account; a not-yet-registered email is rejected (they must sign in "
            "once first). Workspace admin only. Requires tenant:write + "
            "membership:write."
        ),
        input_model=InviteWorkspaceMemberInput,
        output_model=InviteWorkspaceMemberOutput,
        handler=invite_workspace_member,
        required_role="CLIENT",
        required_scopes=("tenant:write", "membership:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
    register(
        name="update_workspace_member_role",
        description=(
            "Change a member's role (viewer/member/manager/admin) in the "
            "caller's workspace. Refuses to demote the last admin. Workspace "
            "admin only. Requires tenant:write + membership:write."
        ),
        input_model=UpdateMemberRoleInput,
        output_model=UpdateMemberRoleOutput,
        handler=update_workspace_member_role,
        required_role="CLIENT",
        required_scopes=("tenant:write", "membership:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="revoke_workspace_member",
        description=(
            "Revoke a member's access to the caller's workspace (sets status "
            "revoked; cuts access immediately under the membership gate). "
            "Refuses to revoke yourself or the last admin. Workspace admin "
            "only. Requires tenant:write + membership:write."
        ),
        input_model=RevokeWorkspaceMemberInput,
        output_model=RevokeWorkspaceMemberOutput,
        handler=revoke_workspace_member,
        required_role="CLIENT",
        required_scopes=("tenant:write", "membership:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="end_tenant_membership",
        description=(
            "Archive a person's per-tenant membership without deleting the "
            "canonical person. Requires STAFF and membership:write."
        ),
        input_model=EndTenantMembershipInput,
        output_model=EndTenantMembershipOutput,
        handler=end_tenant_membership,
        required_role="STAFF",
        required_scopes=("membership:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
    register(
        name="offboard_tenant",
        description=(
            "Offboard the caller's current tenant: purge every person, "
            "anonymize the hash-chained ledger in place, revoke all workspace "
            "memberships, and best-effort delete the tenant's Garage buckets + "
            "drop its FalkorDB graph. Stamps the tenant deprecated and appends a "
            "tenant.offboard event. Irreversible; idempotent (a re-run no-ops). "
            "Requires the literal confirmation phrase 'OFFBOARD TENANT "
            "PERMANENTLY'. ADMIN only; requires tenant:write + person:delete."
        ),
        input_model=OffboardTenantInput,
        output_model=OffboardTenantOutput,
        handler=offboard_tenant,
        required_role="ADMIN",
        required_scopes=("tenant:write", "person:delete"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
    register(
        name="set_organization_tenant_membership",
        description=(
            "Upsert an organization's per-tenant membership. Mirrors "
            "set_tenant_membership for organizations."
        ),
        input_model=SetOrgTenantMembershipInput,
        output_model=SetOrgTenantMembershipOutput,
        handler=set_organization_tenant_membership,
        required_role="STAFF",
        required_scopes=("tenant:write", "membership:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )


register_tenancy_tools()
