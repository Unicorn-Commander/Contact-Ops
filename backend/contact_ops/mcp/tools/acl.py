"""Per-record sharing tools (Phase 2 Codex 1).

Design authority: Contact-Ops-MCP-Design.md §5.13. ``set_visibility`` is a
narrow convenience wrapper that flips the per-tenant membership visibility
without touching notes/tags. ``share_with_user`` / ``revoke_share`` /
``list_acl`` operate on the ``acl_grant`` table introduced by Alembic 0018.
"""
# ruff: noqa: I001

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, or_, select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import (
    PERSON_NOT_FOUND,
    ORG_NOT_FOUND,
    ToolError,
)
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register
from contact_ops.models import (
    AclGrant,
    Organization,
    Person,
    PersonTenantMembership,
)
from contact_ops.models.acl_grant import ACL_SCOPES, ACL_SUBJECT_KINDS

MEMBERSHIP_NOT_FOUND = "MEMBERSHIP_NOT_FOUND"
ACL_NOT_FOUND = "ACL_NOT_FOUND"
ALREADY_REVOKED = "ALREADY_REVOKED"
SUBJECT_NOT_FOUND = "SUBJECT_NOT_FOUND"
ALREADY_GRANTED = "ALREADY_GRANTED"
GRANTEE_REQUIRED = "GRANTEE_REQUIRED"


# ---------------------------------------------------------------------------
# set_visibility
# ---------------------------------------------------------------------------


VISIBILITIES = ("visible", "hidden", "archived")


class SetVisibilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID
    tenant_id: uuid.UUID
    visibility: Literal[VISIBILITIES] = "visible"  # type: ignore[valid-type]
    confidence: float = Field(default=1.0, ge=0, le=1)


class SetVisibilityOutput(ToolOutput):
    person_id: uuid.UUID
    tenant_id: uuid.UUID
    prior_visibility: str
    visibility: str
    status: str
    event_id: uuid.UUID | None = None


async def set_visibility(
    ctx: MCPContext, req: SetVisibilityInput
) -> SetVisibilityOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["membership:write"])
    if req.tenant_id != ctx.tenant_id:
        roles = {
            str(r).upper()
            for r in ctx.claims.get("realm_access", {}).get("roles", [])
        }
        if "ADMIN" not in roles:
            raise ToolError(
                MEMBERSHIP_NOT_FOUND,
                "target tenant does not match caller's tenant",
            )

    membership = await ctx.db.get(
        PersonTenantMembership, (req.person_id, req.tenant_id)
    )
    if membership is None:
        raise ToolError(
            MEMBERSHIP_NOT_FOUND,
            f"no membership for person {req.person_id} in tenant {req.tenant_id}",
        )
    prior = membership.visibility
    if prior == req.visibility:
        return SetVisibilityOutput(
            person_id=req.person_id,
            tenant_id=req.tenant_id,
            prior_visibility=prior,
            visibility=req.visibility,
            status="applied",
        )
    membership.visibility = req.visibility
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="visibility.set",
        aggregate_type="person",
        aggregate_id=req.person_id,
        affected_ids=[req.person_id],
        payload_before={"visibility": prior},
        payload_after={"visibility": req.visibility},
        confidence=req.confidence,
    )
    return SetVisibilityOutput(
        person_id=req.person_id,
        tenant_id=req.tenant_id,
        prior_visibility=prior,
        visibility=req.visibility,
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# list_acl
# ---------------------------------------------------------------------------


class ListAclInput(BaseModel):
    subject_kind: Literal[ACL_SUBJECT_KINDS]  # type: ignore[valid-type]
    subject_id: uuid.UUID
    include_revoked: bool = False


class ListAclOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int


def _acl_summary(grant: AclGrant) -> dict[str, Any]:
    return {
        "acl_id": str(grant.id),
        "tenant_id": str(grant.tenant_id),
        "subject_kind": grant.subject_kind,
        "subject_id": str(grant.subject_id),
        "grantee_user_id": (
            str(grant.grantee_user_id) if grant.grantee_user_id else None
        ),
        "grantee_tenant_id": (
            str(grant.grantee_tenant_id) if grant.grantee_tenant_id else None
        ),
        "scope": grant.scope,
        "reason": grant.reason,
        "granted_at": grant.granted_at,
        "expires_at": grant.expires_at,
        "revoked_at": grant.revoked_at,
    }


async def list_acl(ctx: MCPContext, req: ListAclInput) -> ListAclOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["acl:read"])
    if req.subject_kind == "person":
        subject = await ctx.db.get(Person, req.subject_id)
        if subject is None or subject.canonical_owner_tenant_id != ctx.tenant_id:
            raise ToolError(SUBJECT_NOT_FOUND, "subject not found")
    else:
        org_subject = await ctx.db.get(Organization, req.subject_id)
        if org_subject is None or org_subject.canonical_owner_tenant_id != ctx.tenant_id:
            raise ToolError(SUBJECT_NOT_FOUND, "subject not found")

    stmt = select(AclGrant).where(
        AclGrant.tenant_id == ctx.tenant_id,
        AclGrant.subject_kind == req.subject_kind,
        AclGrant.subject_id == req.subject_id,
    )
    if not req.include_revoked:
        stmt = stmt.where(AclGrant.revoked_at.is_(None))
    rows = (await ctx.db.execute(stmt.order_by(AclGrant.granted_at.desc()))).scalars().all()
    items = [_acl_summary(g) for g in rows]
    return ListAclOutput(items=items, count=len(items))


# ---------------------------------------------------------------------------
# share_with_user
# ---------------------------------------------------------------------------


class ShareWithUserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_kind: Literal[ACL_SUBJECT_KINDS]  # type: ignore[valid-type]
    subject_id: uuid.UUID
    grantee_user_id: uuid.UUID | None = None
    grantee_tenant_id: uuid.UUID | None = None
    scope: Literal[ACL_SCOPES] = "read"  # type: ignore[valid-type]
    reason: str | None = Field(default=None, max_length=500)
    expires_at: datetime | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_grantee(self) -> ShareWithUserInput:
        if (self.grantee_user_id is None) == (self.grantee_tenant_id is None):
            raise ValueError(
                "exactly one of grantee_user_id or grantee_tenant_id must be set"
            )
        return self


class ShareWithUserOutput(ToolOutput):
    acl_id: uuid.UUID
    status: str
    event_id: uuid.UUID | None = None


async def share_with_user(
    ctx: MCPContext, req: ShareWithUserInput
) -> ShareWithUserOutput:
    require_role(ctx, "MANAGER")
    require_scopes(ctx, ["acl:write"])

    if req.subject_kind == "person":
        subject = await ctx.db.get(Person, req.subject_id)
        if subject is None or subject.canonical_owner_tenant_id != ctx.tenant_id:
            raise ToolError(SUBJECT_NOT_FOUND, "subject not found in this tenant")
    else:
        org_subject = await ctx.db.get(Organization, req.subject_id)
        if (
            org_subject is None
            or org_subject.canonical_owner_tenant_id != ctx.tenant_id
        ):
            raise ToolError(SUBJECT_NOT_FOUND, "subject not found in this tenant")

    grantee_clause = (
        AclGrant.grantee_user_id == req.grantee_user_id
        if req.grantee_user_id
        else AclGrant.grantee_tenant_id == req.grantee_tenant_id
    )
    existing = await ctx.db.scalar(
        select(AclGrant).where(
            AclGrant.tenant_id == ctx.tenant_id,
            AclGrant.subject_kind == req.subject_kind,
            AclGrant.subject_id == req.subject_id,
            AclGrant.scope == req.scope,
            AclGrant.revoked_at.is_(None),
            grantee_clause,
        )
    )
    if existing is not None:
        return ShareWithUserOutput(acl_id=existing.id, status="noop")

    actor_uuid: uuid.UUID | None = None
    try:
        actor_uuid = uuid.UUID(ctx.user_id)
    except (TypeError, ValueError):
        actor_uuid = None

    grant = AclGrant(
        tenant_id=ctx.tenant_id,
        subject_kind=req.subject_kind,
        subject_id=req.subject_id,
        grantee_user_id=req.grantee_user_id,
        grantee_tenant_id=req.grantee_tenant_id,
        scope=req.scope,
        reason=req.reason,
        expires_at=req.expires_at,
        granted_by_actor_id=actor_uuid,
    )
    ctx.db.add(grant)
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="acl.share",
        aggregate_type=(
            "person" if req.subject_kind == "person" else "organization"
        ),
        aggregate_id=req.subject_id,
        affected_ids=[req.subject_id],
        payload_before=None,
        payload_after=_acl_summary(grant),
        confidence=req.confidence,
    )
    return ShareWithUserOutput(
        acl_id=grant.id, status="applied", event_id=event_id
    )


# ---------------------------------------------------------------------------
# revoke_share
# ---------------------------------------------------------------------------


class RevokeShareInput(BaseModel):
    acl_id: uuid.UUID
    confidence: float = Field(default=1.0, ge=0, le=1)


class RevokeShareOutput(ToolOutput):
    acl_id: uuid.UUID
    revoked_at: datetime | None
    status: str
    event_id: uuid.UUID | None = None


async def revoke_share(
    ctx: MCPContext, req: RevokeShareInput
) -> RevokeShareOutput:
    require_role(ctx, "MANAGER")
    require_scopes(ctx, ["acl:write"])

    grant = await ctx.db.get(AclGrant, req.acl_id)
    if grant is None or grant.tenant_id != ctx.tenant_id:
        raise ToolError(ACL_NOT_FOUND, "acl grant not found")
    if grant.revoked_at is not None:
        return RevokeShareOutput(
            acl_id=grant.id, revoked_at=grant.revoked_at, status="noop"
        )

    actor_uuid: uuid.UUID | None = None
    try:
        actor_uuid = uuid.UUID(ctx.user_id)
    except (TypeError, ValueError):
        actor_uuid = None

    before = _acl_summary(grant)
    grant.revoked_at = datetime.now().astimezone()
    grant.revoked_by_actor_id = actor_uuid
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="acl.revoke",
        aggregate_type=(
            "person" if grant.subject_kind == "person" else "organization"
        ),
        aggregate_id=grant.subject_id,
        affected_ids=[grant.subject_id],
        payload_before=before,
        payload_after=_acl_summary(grant),
        confidence=req.confidence,
    )
    return RevokeShareOutput(
        acl_id=grant.id,
        revoked_at=grant.revoked_at,
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


def register_acl_tools() -> None:
    register(
        name="set_visibility",
        description=(
            "Set the per-tenant membership visibility for a person "
            "(visible/hidden/archived). Convenience wrapper around "
            "set_tenant_membership."
        ),
        input_model=SetVisibilityInput,
        output_model=SetVisibilityOutput,
        handler=set_visibility,
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
        name="list_acl",
        description=(
            "List active (and optionally revoked) per-record share grants "
            "for a person or organization. Requires STAFF and acl:read."
        ),
        input_model=ListAclInput,
        output_model=ListAclOutput,
        handler=list_acl,
        required_role="STAFF",
        required_scopes=("acl:read",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="share_with_user",
        description=(
            "Grant a user or peer tenant time-bounded access to a specific "
            "person or organization. Requires MANAGER and acl:write."
        ),
        input_model=ShareWithUserInput,
        output_model=ShareWithUserOutput,
        handler=share_with_user,
        required_role="MANAGER",
        required_scopes=("acl:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
    register(
        name="revoke_share",
        description=(
            "Revoke an existing per-record share grant. Requires MANAGER "
            "and acl:write."
        ),
        input_model=RevokeShareInput,
        output_model=RevokeShareOutput,
        handler=revoke_share,
        required_role="MANAGER",
        required_scopes=("acl:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )


register_acl_tools()


# Lightweight no-op references to avoid unused-import lints in pyright/mypy.
_ = (and_, or_, ORG_NOT_FOUND, PERSON_NOT_FOUND, ALREADY_GRANTED,
     ALREADY_REVOKED, GRANTEE_REQUIRED)
