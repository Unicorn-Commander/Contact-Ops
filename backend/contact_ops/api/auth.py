"""Auth endpoints: workspace-switch (Phase 4.3) + self-serve onboarding (Phase 4.4).

`POST /api/auth/onboard` (Phase 4.4) is the public self-serve entry point. On a
human's FIRST authenticated request — before they belong to any workspace — it
materializes their personal workspace via the `provision_personal_workspace`
SECURITY DEFINER function (migration 0038), writes the resulting tenant_id /
tenant_slug back onto the Keycloak user (so the next signinSilent mints a
tenant-bearing token, exactly like switch-workspace), and returns the tenant.
It is IDEMPOTENT: a user who already has an active membership gets their existing
home tenant back with `provisioned: false` and no second workspace is created.
It REUSES switch-workspace's Keycloak attribute-write helper, its
INERT-503-without-secret posture, its 502-on-KC-error collapse (no secret ever
logged), and its audit pattern (a `tenant.onboard` action_event). This is the
ONE route the JWT middleware lets a verified-but-tenant-less Keycloak token reach
(see middleware/jwt_validation.py: ONBOARD_PATH + allow_missing_tenant).

`POST /api/auth/switch-workspace` changes the *active workspace* of a human
Keycloak session. The mechanism is **silent re-auth**, NOT token-exchange:

  1. The caller presents their current bearer (a Keycloak access token); the
     JWTValidationMiddleware has already validated it and placed the claims on
     `request.state.jwt_claims` (incl. the normalized `uc_uid`).
  2. We REJECT non-Keycloak issuers (PAT / Brigade / standalone): those identities
     are single-tenant by construction and have no switchable attribute.
  3. We authorize the switch from `user_tenant_membership` (ground truth, via the
     `user_is_active_member` SECURITY DEFINER predicate) — NOT from the request
     body or any claim. No active membership for (uc_uid, target) -> 403.
  4. If the target workspace is HIPAA or strict-isolation, we DO NOT switch; we
     return 401 `{step_up_required: true}` so the SPA can do a per-workspace
     step-up (signinRedirect with max_age=0) and retry.
  5. Otherwise we use the confidential `contact-ops-switch` client
     (client-credentials grant) against the Keycloak Admin REST API to set the
     user's `tenant_id` / `tenant_slug` attributes (GET-merge-PUT: KC 25 has no
     partial user update — a PUT must carry the full representation or it drops
     the omitted attributes).
  6. The SPA then performs `signinSilent()` (prompt=none) so Keycloak re-runs the
     existing `contact-ops-app` User Attribute mappers against the freshly-written
     attributes and mints a new token carrying the new `tenant_id` / `tenant_slug`
     (and aud=contact-ops-mcp). This endpoint does NOT mint a token itself.

Why silent re-auth and not RFC 8693 token-exchange: bigboy's Keycloak is 25.0.6,
where standard token-exchange is not GA. Silent re-auth is version-agnostic and
needs no token-exchange permission setup. See
`~/Documents/Contact-Ops-Phase4.3-Switch-and-Keycloak-Ops.md` §2-3 and
`backend/scripts/keycloak/setup_contact_ops_switch.sh`.

Safety properties:
  * Membership is the gate (design §4.4) — checked against the DEFINER function so
    it is correct regardless of the session's GUC/RLS state.
  * If `KEYCLOAK_SWITCH_CLIENT_SECRET` is unset the endpoint is INERT (503), so it
    is safe to deploy this code before the Keycloak client exists.
  * Keycloak errors never leak the client secret or KC internals: every failure
    collapses to a generic 502.
  * A `tenant.switch` action_event is written via the audit role (mirroring
    `middleware/audit.py`'s direct INSERT) under the caller's CURRENT workspace,
    so the switch attempt is auditable from the workspace the caller actually
    held when they made it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any
from urllib.parse import quote

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text

from contact_ops.core.config import Settings, get_settings
from contact_ops.core.database import (
    _user_is_active_member,
    async_session_maker,
    bind_session_context,
    get_audit_db_context,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# Error codes returned in `detail` so the SPA can branch deterministically.
NOT_A_MEMBER = "NOT_A_MEMBER"
TENANT_NOT_FOUND = "TENANT_NOT_FOUND"

# Keycloak Admin REST + token call timeout. Matches the connectors' house value.
_KC_TIMEOUT_SECONDS = 30


class SwitchWorkspaceRequest(BaseModel):
    target_tenant_id: uuid.UUID


class SwitchWorkspaceResponse(BaseModel):
    ok: bool
    tenant_id: uuid.UUID
    tenant_slug: str


class OnboardResponse(BaseModel):
    tenant_id: uuid.UUID
    tenant_slug: str
    # False when the user already had an active membership (idempotent replay);
    # True when this call materialized a brand-new personal workspace.
    provisioned: bool


class SignupConfigResponse(BaseModel):
    signup_enabled: bool
    signup_mode: str  # "suite" | "standalone"
    suite_signup_url: str | None = None


@router.get("/signup-config", response_model=SignupConfigResponse)
async def signup_config() -> SignupConfigResponse:
    """Public (no auth): tells the login screen whether to surface a signup CTA
    and in which mode — "suite" (sign in + link to the UC suite signup, which
    owns accounts/subscriptions) or "standalone" (CO's own Keycloak self-
    registration). Returns ONLY feature flags + the suite signup URL; no tenant
    data, no secrets. Backend-driven so flipping signup on needs no SPA rebuild.
    """
    settings = get_settings()
    return SignupConfigResponse(
        signup_enabled=settings.SIGNUP_ENABLED,
        signup_mode=settings.SIGNUP_MODE,
        suite_signup_url=settings.SUITE_SIGNUP_URL or None,
    )


@router.post(
    "/switch-workspace",
    response_model=SwitchWorkspaceResponse,
    summary="Set the caller's active workspace (silent re-auth mechanism)",
)
async def switch_workspace(
    request: Request, body: SwitchWorkspaceRequest
) -> SwitchWorkspaceResponse:
    settings = get_settings()
    claims = getattr(request.state, "jwt_claims", None)
    if not isinstance(claims, dict) or not claims.get("tenant_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="tenant context unresolved",
        )

    # (1) Reject non-Keycloak issuers. PAT / Brigade / standalone tokens are
    # single-tenant by construction: they carry no switchable user attribute and
    # there is no Keycloak user to update. The active workspace is only a
    # meaningful concept for an interactive Keycloak session.
    if claims.get("iss") != settings.KEYCLOAK_ISSUER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace switch is only available for interactive sessions",
        )

    # `uc_uid` is normalized to a guaranteed-present value (falls back to `sub`)
    # by JWTValidationMiddleware; read it from the same claims source.
    uc_uid = str(claims.get("uc_uid") or claims.get("sub") or "")
    if not uc_uid:
        # Defensive: the middleware fails closed on a missing uc_uid, so this is a
        # programming error, but never proceed without an identity.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user identity unresolved",
        )

    target_tenant_id = str(body.target_tenant_id)
    from_tenant_id = str(claims["tenant_id"])

    # (2 + 3) Authorize + resolve the target in a single short-lived session.
    # We bind app.uc_uid (and the caller's CURRENT tenant for app.tenant_id) so
    # any uc_uid-aware RLS has the value, then check membership via the SECURITY
    # DEFINER predicate with EXPLICIT args (we do NOT depend on a tenant GUC we
    # have not bound for the *target* — the DEFINER function is correct
    # regardless of GUC/RLS state). The tenant lookup uses a DEFINER-style
    # cross-tenant read guarded by the prior membership check.
    async with async_session_maker() as session:
        await bind_session_context(session, from_tenant_id, uc_uid, settings)

        is_member = await _user_is_active_member(session, uc_uid, target_tenant_id)
        if not is_member:
            # Do NOT distinguish "tenant does not exist" from "you are not a
            # member": both collapse to NOT_A_MEMBER so a caller cannot probe for
            # the existence of workspaces they do not belong to.
            logger.info(
                "workspace_switch_denied",
                reason="not_a_member",
                uc_uid=uc_uid,
                target_tenant_id=target_tenant_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=NOT_A_MEMBER,
            )

        # Membership confirmed -> safe to read the target's switch-relevant
        # fields. The read re-gates on user_is_active_member (DEFINER) inside the
        # WHERE so it works regardless of the session's bound tenant (we are bound
        # to `from_tenant`, not the target) AND cannot return a tenant the caller
        # is not a member of even if this call site is ever refactored.
        target = await _load_target_tenant(session, uc_uid, target_tenant_id)

    if target is None:
        # Membership row referenced a tenant that no longer exists (e.g. deleted
        # mid-flight). Treat as a clean not-found rather than a 500.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=TENANT_NOT_FOUND,
        )

    tenant_slug = str(target["slug"])
    hipaa_mode = bool(target["hipaa_mode"])
    isolation_mode = str(target["isolation_mode"])

    # (4) Step-up gate. A HIPAA or strict-isolation workspace requires a fresh,
    # per-workspace authentication; we refuse the silent path and tell the SPA to
    # do a full step-up (signinRedirect max_age=0) and retry. We do NOT touch the
    # Keycloak attribute in this case.
    if hipaa_mode or isolation_mode == "strict":
        logger.info(
            "workspace_switch_step_up_required",
            uc_uid=uc_uid,
            target_tenant_id=target_tenant_id,
            hipaa_mode=hipaa_mode,
            isolation_mode=isolation_mode,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"step_up_required": True},
        )

    # (5) Inert-until-configured guard. Deploying this code before the Keycloak
    # `contact-ops-switch` client exists is safe: with no secret we cannot (and
    # must not) attempt an admin write.
    if not settings.KEYCLOAK_SWITCH_CLIENT_SECRET:
        logger.warning("workspace_switch_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="switch not configured",
        )

    # (6) Set the user's tenant_id / tenant_slug attributes via the confidential
    # contact-ops-switch service account. On ANY Keycloak failure -> 502 with a
    # safe message (the secret / KC internals never leak).
    try:
        await _set_keycloak_tenant_attributes(
            settings,
            uc_uid=uc_uid,
            tenant_id=target_tenant_id,
            tenant_slug=tenant_slug,
        )
    except _KeycloakError as exc:
        logger.warning(
            "workspace_switch_keycloak_failed",
            uc_uid=uc_uid,
            target_tenant_id=target_tenant_id,
            stage=exc.stage,
            # exc.detail is an operator-facing summary; it never contains the
            # client secret or token (see _KeycloakError construction sites).
            detail=exc.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="identity provider update failed",
        ) from exc

    # Stamp the audit event AFTER the attribute write succeeds. A failure here
    # must not undo the switch nor 500 the caller, so it is best-effort (mirrors
    # middleware/audit.py: audit must never break the response).
    await _emit_switch_audit_event(
        request,
        claims=claims,
        uc_uid=uc_uid,
        from_tenant_id=from_tenant_id,
        to_tenant_id=target_tenant_id,
        to_tenant_slug=tenant_slug,
    )

    logger.info(
        "workspace_switch_applied",
        uc_uid=uc_uid,
        from_tenant_id=from_tenant_id,
        to_tenant_id=target_tenant_id,
    )
    # The SPA reads {ok, tenant_id, tenant_slug} then does signinSilent() to pick
    # up a fresh token whose claims reflect the new attributes.
    return SwitchWorkspaceResponse(
        ok=True,
        tenant_id=body.target_tenant_id,
        tenant_slug=tenant_slug,
    )


@router.post(
    "/onboard",
    response_model=OnboardResponse,
    summary="Provision the caller's personal workspace on first login (self-serve)",
)
async def onboard(request: Request) -> OnboardResponse:
    """Materialize the caller's first personal workspace (idempotent).

    Reached by a Keycloak token that has passed FULL signature/issuer/audience
    verification in the middleware but may lack a tenant_id (the middleware lets
    a tenant-less verified KC token reach ONLY this route — see
    middleware/jwt_validation.py). Flow:

      1. Require a Keycloak-issued token (iss == KEYCLOAK_ISSUER). PAT / Brigade
         / standalone identities are single-tenant by construction and have no
         Keycloak user to provision against -> 400. This mirrors switch-workspace
         and is a second, independent issuer check (defense in depth: even though
         the middleware allowance is KC-only, the handler does not trust that).
      2. uc_uid is the middleware-pinned `sub` (the membership key). Fail-closed
         if absent.
      3. Idempotency: if the user already has ANY active membership, return their
         home tenant {tenant_id, tenant_slug, provisioned: false} — do NOT create
         a second workspace. (The DB function is itself idempotent under an
         advisory lock; this is the cheap pre-check so the happy replay path does
         no KC write at all.)
      4. Otherwise call provision_personal_workspace(uc_uid, preferred_username,
         email), then set the new tenant_id / tenant_slug on the KC user via the
         SAME confidential contact-ops-switch admin write switch-workspace uses,
         so a subsequent signinSilent mints a tenant-bearing token. INERT-503 if
         the switch client secret is unset; any KC failure -> 502 (secret never
         leaks). Emit a `tenant.onboard` audit event. Return provisioned: true.
    """
    settings = get_settings()
    claims = getattr(request.state, "jwt_claims", None)
    if not isinstance(claims, dict):
        # The middleware sets jwt_claims on every authenticated request; absence
        # is a programming error, but fail closed.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user identity unresolved",
        )

    # (1) Keycloak-only. Onboarding provisions a *Keycloak* user's first
    # workspace; non-KC issuers have no switchable/provisionable attribute.
    if claims.get("iss") != settings.KEYCLOAK_ISSUER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="onboarding is only available for interactive sessions",
        )

    # (2) `uc_uid` is pinned to `sub` by the middleware and is the membership key.
    uc_uid = str(claims.get("uc_uid") or claims.get("sub") or "")
    if not uc_uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user identity unresolved",
        )

    preferred_username = str(claims.get("preferred_username") or "")
    email = str(claims.get("email") or "")

    # (3) Idempotent fast path + (4) provisioning, in a single short-lived
    # session. We bind uc_uid for attribution but DELIBERATELY leave tenant
    # unbound at first (current_tenant_id() -> NULL): we do not yet know which
    # workspace is the caller's home. The membership pre-check reads
    # `user_tenant_membership` ALONE (uc_uid-keyed; readable under utm_tenant_scope
    # RLS which keys on app.uc_uid) — it does NOT join `tenants`, because the
    # `tenants` SELECT policy is `id = current_tenant_id()` and with no tenant
    # bound that is NULL-over-deny (0034's noted recursion/NULL trap). The
    # provisioning function is DEFINER and bypasses RLS regardless, so its INSERTs
    # do not depend on a bound GUC. Only AFTER we know the tenant id (existing or
    # freshly provisioned) do we bind to it and read its slug through RLS.
    provisioned = False
    async with async_session_maker() as session:
        await bind_session_context(session, "", uc_uid, settings)

        resolved_tenant_id = await _user_active_membership_tenant(session, uc_uid)
        if resolved_tenant_id is None:
            # No membership -> provision. The DEFINER function takes its own
            # advisory lock and is itself idempotent, so a race that slipped past
            # the pre-check still yields exactly one workspace and returns that
            # tenant id (we'd then report provisioned=true for it, which is the
            # correct semantic: this request observed no prior membership).
            resolved_tenant_id = await _provision_personal_workspace(
                session, uc_uid=uc_uid, username=preferred_username, email=email
            )
            provisioned = True

        if not resolved_tenant_id:
            # Provisioning returned no tenant (e.g. a non-uuid sub the
            # owner_user_id NOT NULL constraint refused). Surface a clean 500
            # rather than a half state; nothing actionable was written.
            await session.rollback()
            logger.error("onboard_provision_returned_no_tenant", uc_uid=uc_uid)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="workspace provisioning failed",
            )

        tenant_id_str = str(resolved_tenant_id)

        # Now that we know the caller's tenant (and they are its member/owner),
        # bind to it so the `tenants` SELECT policy admits the slug read.
        await bind_session_context(session, tenant_id_str, uc_uid, settings)
        new_slug = await _tenant_slug(session, tenant_id_str)
        await session.commit()

    if not new_slug:
        # The membership/provision pointed at a tenant whose slug we cannot read
        # back even bound to it (deleted mid-flight, or never created). Fail clean.
        logger.error(
            "onboard_slug_unreadable", uc_uid=uc_uid, tenant_id=tenant_id_str
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="workspace provisioning failed",
        )

    if not provisioned:
        # Idempotent replay: the user already had an active membership. We do NOT
        # create a second workspace or emit a provision audit event. BUT we DO
        # backfill the Keycloak tenant attribute when the caller's token carries
        # no tenant_id at all (a legacy user whose home workspace predates the
        # KC-attribute mechanism, or whose attribute was cleared). Without this
        # they are stuck forever: /mcp fail-closes on a missing tenant_id claim,
        # and the provision path that writes the attribute never runs for an
        # already-provisioned user. We only backfill when the claim is ABSENT,
        # never overwriting a workspace the user actively switched into. Same
        # configured-guard + KC-failure posture as the provision path below.
        if not claims.get("tenant_id"):
            if not settings.KEYCLOAK_SWITCH_CLIENT_SECRET:
                logger.warning("onboard_backfill_not_configured", uc_uid=uc_uid)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="onboarding not configured",
                )
            try:
                await _set_keycloak_tenant_attributes(
                    settings,
                    uc_uid=uc_uid,
                    tenant_id=tenant_id_str,
                    tenant_slug=new_slug,
                )
                logger.info(
                    "onboard_kc_attribute_backfilled",
                    uc_uid=uc_uid,
                    tenant_id=tenant_id_str,
                )
            except _KeycloakError as exc:
                logger.warning(
                    "onboard_kc_backfill_failed",
                    uc_uid=uc_uid,
                    tenant_id=tenant_id_str,
                    stage=exc.stage,
                    detail=exc.detail,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="identity provider update failed",
                ) from exc
        logger.info(
            "onboard_idempotent_replay", uc_uid=uc_uid, tenant_id=tenant_id_str
        )
        return OnboardResponse(
            tenant_id=resolved_tenant_id,
            tenant_slug=new_slug,
            provisioned=False,
        )

    new_tenant_id = resolved_tenant_id

    # (4) Inert-until-configured guard — identical posture to switch-workspace.
    # If the contact-ops-switch client secret is unset we must NOT attempt a KC
    # admin write. The workspace + membership already exist (committed above), so
    # a later retry once the secret is configured will take the idempotent path
    # and only do the attribute write. 503 signals "try again once configured".
    if not settings.KEYCLOAK_SWITCH_CLIENT_SECRET:
        logger.warning("onboard_not_configured", uc_uid=uc_uid)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="onboarding not configured",
        )

    # Set tenant_id / tenant_slug on the KC user via the SAME confidential
    # contact-ops-switch service account switch-workspace uses. Any KC failure
    # collapses to 502 with a safe message (secret / KC internals never leak).
    try:
        await _set_keycloak_tenant_attributes(
            settings,
            uc_uid=uc_uid,
            tenant_id=tenant_id_str,
            tenant_slug=new_slug,
        )
    except _KeycloakError as exc:
        logger.warning(
            "onboard_keycloak_failed",
            uc_uid=uc_uid,
            tenant_id=tenant_id_str,
            stage=exc.stage,
            # exc.detail is operator-facing and never contains the secret/token.
            detail=exc.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="identity provider update failed",
        ) from exc

    # Best-effort audit AFTER the KC write succeeds (audit must never break the
    # successful response), bound to the NEW tenant the caller now owns.
    await _emit_onboard_audit_event(
        request,
        claims=claims,
        uc_uid=uc_uid,
        tenant_id=tenant_id_str,
        tenant_slug=new_slug,
    )

    # Auto-provision the tenant's Garage buckets (photos/voice/etc) so uploads
    # work for this self-served tenant. DORMANT by default (no-ops unless
    # GARAGE_AUTO_PROVISION is on with a real admin token + access key), and
    # strictly BEST-EFFORT: any failure degrades uploads (a later backfill
    # repairs) but must NEVER fail a successful signup.
    try:
        from contact_ops.services.garage_lifecycle import provision_tenant_storage

        await provision_tenant_storage(tenant_slug=new_slug, hipaa_mode=False, settings=settings)
    except Exception as exc:  # noqa: BLE001 — storage provisioning is best-effort
        logger.warning(
            "onboard_garage_provision_failed",
            uc_uid=uc_uid,
            tenant_id=tenant_id_str,
            error=str(exc),
        )

    logger.info("onboard_provisioned", uc_uid=uc_uid, tenant_id=tenant_id_str)
    return OnboardResponse(
        tenant_id=new_tenant_id,
        tenant_slug=new_slug,
        provisioned=True,
    )


# ---------------------------------------------------------------------------
# Onboarding DB helpers (membership pre-check, provisioning, slug read)
# ---------------------------------------------------------------------------


async def _user_active_membership_tenant(
    session: Any, uc_uid: str
) -> uuid.UUID | None:
    """Return the caller's home tenant id (active membership) or None.

    Reads ``user_tenant_membership`` ALONE — no ``tenants`` JOIN — so it is
    correct for a tenant-LESS session. The table's RLS (utm_tenant_scope, 0035)
    keys on ``app.uc_uid`` (which we bound) and the WHERE filters on the caller's
    own uc_uid, so a caller can only ever observe their OWN memberships. We do
    NOT join ``tenants`` here because that table's SELECT policy is
    ``id = current_tenant_id()``; with no tenant bound that is NULL and would
    deny every row (0034's NULL-over-deny trap). The slug is read separately,
    AFTER binding to the resolved tenant.

    "Home" uses the SAME ordering provision_personal_workspace's idempotent fast
    path uses (``is_default DESC, added_at ASC``) so the onboard pre-check and the
    DB function agree on which membership is canonical. This is only the cheap
    idempotent pre-check; the DEFINER function remains the authoritative
    race-safe path.
    """
    row = await session.execute(
        text(
            """
            SELECT tenant_id
            FROM user_tenant_membership
            WHERE user_uc_uid = :uc
              AND status = 'active'
            ORDER BY is_default DESC, added_at ASC
            LIMIT 1
            """
        ),
        {"uc": uc_uid},
    )
    return row.scalar()


async def _provision_personal_workspace(
    session: Any, *, uc_uid: str, username: str, email: str
) -> uuid.UUID | None:
    """Call the migration-0038 SECURITY DEFINER provisioning function.

    Returns the (new or race-resolved existing) tenant id. The function is
    DEFINER + advisory-locked + idempotent: it creates exactly one
    kind='personal', standard-isolation, non-HIPAA workspace owned by ``uc_uid``
    plus a single self admin membership, or returns the existing tenant if one
    already exists. Free-text ``username`` / ``email`` are passed as bound
    parameters and consumed only as plpgsql values (no dynamic SQL).
    """
    row = await session.execute(
        text(
            "SELECT provision_personal_workspace(:uc, :uname, :email) AS tenant_id"
        ),
        {"uc": uc_uid, "uname": username, "email": email},
    )
    return row.scalar()


async def _tenant_slug(session: Any, tenant_id: uuid.UUID | str) -> str | None:
    """Read a tenant's slug by id (post-provision read-back).

    No membership re-gate is needed: the caller was just provisioned as this
    tenant's admin (or it is their existing home tenant), and the id came from
    the DEFINER function in this same transaction.
    """
    row = await session.execute(
        text(
            """
            SELECT slug
            FROM tenants
            WHERE id = CAST(:tid AS uuid)
              AND deprecated_at IS NULL
            """
        ),
        {"tid": str(tenant_id)},
    )
    value = row.scalar()
    return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# Target-tenant lookup (membership-gated cross-tenant read)
# ---------------------------------------------------------------------------


async def _load_target_tenant(
    session: Any, uc_uid: str, tenant_id: str
) -> dict[str, Any] | None:
    """Read the target tenant's switch-relevant fields.

    Returns a mapping with ``slug``, ``hipaa_mode``, ``isolation_mode`` or
    ``None`` if the tenant does not exist, is deprecated, or the caller is not an
    active member. Called only after the handler has already confirmed
    membership; the ``user_is_active_member`` predicate in the WHERE re-gates the
    row so this read cannot return a tenant the caller is not an active member of
    even if the call site is ever refactored — defense in depth.

    ``user_is_active_member`` is SECURITY DEFINER, so the read is correct
    regardless of the session's bound tenant (we are bound to ``from_tenant``,
    not the target). ``isolation_mode`` is on ``tenants`` since migration 0034;
    ``hipaa_mode`` since 0002.
    """
    row = await session.execute(
        text(
            """
            SELECT slug, hipaa_mode, isolation_mode
            FROM tenants
            WHERE id = CAST(:tid AS uuid)
              AND deprecated_at IS NULL
              AND user_is_active_member(:uc, id)
            """
        ),
        {"tid": tenant_id, "uc": uc_uid},
    )
    return row.mappings().first()


# ---------------------------------------------------------------------------
# Keycloak Admin (client-credentials -> GET user -> merge -> PUT user)
# ---------------------------------------------------------------------------


class _KeycloakError(Exception):
    """Internal: any Keycloak interaction failure. Carries a SAFE detail string.

    `detail` is for operator logs only and is constructed to never include the
    client secret or any bearer token. The HTTP layer maps this to a generic 502.
    """

    def __init__(self, stage: str, detail: str) -> None:
        super().__init__(f"{stage}: {detail}")
        self.stage = stage
        self.detail = detail


def _kc_token_url(settings: Settings) -> str:
    # KEYCLOAK_ISSUER is the realm URL, e.g.
    # https://auth.magicunicorn.dev/realms/uchub  ->  + /protocol/openid-connect/token
    return f"{settings.KEYCLOAK_ISSUER.rstrip('/')}/protocol/openid-connect/token"


def _kc_admin_user_url(settings: Settings, uc_uid: str) -> str:
    # Derive the Admin REST base from the issuer by swapping `/realms/<r>` for
    # `/admin/realms/<r>`. The issuer is `<base>/realms/<realm>`; KC's admin API
    # for the same realm lives at `<base>/admin/realms/<realm>`.
    issuer = settings.KEYCLOAK_ISSUER.rstrip("/")
    marker = "/realms/"
    idx = issuer.find(marker)
    if idx == -1:
        # Should never happen for a well-formed uchub issuer; fail safe rather
        # than build a malformed URL.
        raise _KeycloakError("derive-admin-url", "issuer is not a realm URL")
    base = issuer[:idx]
    realm = issuer[idx + len(marker):]
    # Review should-fix #1: percent-encode uc_uid into the path segment. On the
    # KC path uc_uid is signature-verified (uchub `sub` = UUID), so this is not
    # currently exploitable, but quoting removes a whole latent class of
    # wrong-resource GET/PUT if a uc_uid mapper ever emitted `/`, `?`, `#`, `%`.
    return f"{base}/admin/realms/{realm}/users/{quote(uc_uid, safe='')}"


async def _set_keycloak_tenant_attributes(
    settings: Settings,
    *,
    uc_uid: str,
    tenant_id: str,
    tenant_slug: str,
) -> None:
    """Set the user's tenant_id / tenant_slug attributes without clobbering others.

    Three-step GET-merge-PUT, all async over a single httpx client:

      1. POST client_credentials to mint a service-account admin token.
      2. GET /admin/realms/<realm>/users/<uc_uid> to read the full
         UserRepresentation (KC 25 has no partial-attribute endpoint).
      3. PUT the SAME representation back with tenant_id / tenant_slug overwritten
         and EVERY other attribute (and the root fields) preserved, or KC drops
         the omitted attributes (KC 25 upgrading guide; issue keycloak#28220).

    Raises `_KeycloakError` (mapped to 502) on any non-success. Never logs or
    raises the secret/token.
    """
    async with httpx.AsyncClient(timeout=_KC_TIMEOUT_SECONDS) as client:
        admin_token = await _mint_admin_token(client, settings)
        auth_header = {"Authorization": f"Bearer {admin_token}"}

        user_url = _kc_admin_user_url(settings, uc_uid)

        # --- GET the full user representation ---
        try:
            get_resp = await client.get(user_url, headers=auth_header)
        except httpx.HTTPError as exc:
            raise _KeycloakError("get-user", f"transport error: {type(exc).__name__}") from exc
        if get_resp.status_code == 404:
            raise _KeycloakError("get-user", "user not found in realm")
        if get_resp.status_code != 200:
            raise _KeycloakError("get-user", f"status {get_resp.status_code}")
        try:
            user_repr: dict[str, Any] = get_resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise _KeycloakError("get-user", "non-JSON response") from exc
        if not isinstance(user_repr, dict):
            raise _KeycloakError("get-user", "unexpected representation shape")

        # --- merge: overwrite only tenant_id / tenant_slug, preserve the rest ---
        # `attributes` is a map of name -> [string,...]. Single-valued attributes
        # are still a one-element list. Copy the existing map so we never send
        # `attributes: {}` (which would delete every attribute).
        attributes = user_repr.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        else:
            attributes = dict(attributes)
        attributes["tenant_id"] = [tenant_id]
        attributes["tenant_slug"] = [tenant_slug]
        user_repr["attributes"] = attributes

        # --- PUT the full (merged) representation back ---
        try:
            put_resp = await client.put(
                user_url,
                headers={**auth_header, "Content-Type": "application/json"},
                content=json.dumps(user_repr),
            )
        except httpx.HTTPError as exc:
            raise _KeycloakError("put-user", f"transport error: {type(exc).__name__}") from exc
        # KC returns 204 No Content on a successful user update.
        if put_resp.status_code not in (204, 200):
            raise _KeycloakError("put-user", f"status {put_resp.status_code}")


async def _mint_admin_token(
    client: httpx.AsyncClient, settings: Settings
) -> str:
    """client_credentials grant for the contact-ops-switch service account.

    Returns the access_token string. Raises `_KeycloakError` on failure. The
    client secret is sent in the form body only; it is never logged or returned.
    """
    try:
        resp = await client.post(
            _kc_token_url(settings),
            data={
                "grant_type": "client_credentials",
                "client_id": settings.KEYCLOAK_SWITCH_CLIENT_ID,
                "client_secret": settings.KEYCLOAK_SWITCH_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        raise _KeycloakError("token", f"transport error: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        # A 401 here usually means a wrong/rotated secret or the client is not yet
        # a confidential service-account client. Do NOT echo the body (it can
        # contain hints we don't want surfaced); the status is enough for ops.
        raise _KeycloakError("token", f"status {resp.status_code}")
    try:
        token = resp.json().get("access_token")
    except (json.JSONDecodeError, ValueError) as exc:
        raise _KeycloakError("token", "non-JSON token response") from exc
    if not token or not isinstance(token, str):
        raise _KeycloakError("token", "no access_token in response")
    return token


# ---------------------------------------------------------------------------
# Audit: tenant.switch action_event (direct INSERT via the audit role)
# ---------------------------------------------------------------------------


async def _emit_switch_audit_event(
    request: Request,
    *,
    claims: dict[str, Any],
    uc_uid: str,
    from_tenant_id: str,
    to_tenant_id: str,
    to_tenant_slug: str,
) -> None:
    """Write a `tenant.switch` action_event via the audit role.

    Mirrors `middleware/audit.py`: a direct INSERT into `action_event` under a
    tenant-bound `contact_ops_audit` session, matching the schema's NOT NULL
    columns. We do NOT use `mcp.audit_helper.emit_action_event` because that
    requires an `MCPContext` (with both an app `db` and an `audit_db` session
    plus content-hash chaining) which an API route does not have.

    The row's `tenant_id` is the caller's CURRENT (from) workspace — the context
    the caller actually held at switch time and the only one the audit session
    can satisfy the `ae_audit_insert` WITH CHECK for. `aggregate_type` is
    `tenant`; `aggregate_id` is the TARGET tenant. The from/to pair lives in the
    payload so the switch is fully reconstructable.

    Best-effort: any failure is logged and swallowed (audit must never break the
    successful switch response).

    Audit-semantics note (review should-fix #2/#3, accepted as follow-ups):
    `AuditMiddleware` also writes a generic `http.POST /api/auth/switch-workspace`
    event for EVERY authenticated call to this route, including the 403/401/503
    denied paths. This `tenant.switch` event is written ONLY on a successful
    switch. So audit consumers must treat `event_type='tenant.switch'` as the
    SUCCESS count and the generic `http.POST` events as the ATTEMPT count (do not
    double-count). A persistent failure here logs `workspace_switch_audit_failed`;
    wiring that to a metric/alert is the tracked observability follow-up.
    """
    try:
        actor_chain = getattr(request.state, "actor_chain", {"sub": claims.get("sub")})
        human_authority = getattr(
            request.state, "human_authority", claims.get("sub")
        ) or uc_uid

        payload: dict[str, Any] = {
            "from_tenant_id": from_tenant_id,
            "to_tenant_id": to_tenant_id,
            "to_tenant_slug": to_tenant_slug,
            "mechanism": "silent-reauth",
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(payload_json.encode("utf-8")).digest()

        async with get_audit_db_context(
            tenant_id=from_tenant_id, uc_uid=uc_uid
        ) as db:
            await db.execute(
                text(
                    """
                    INSERT INTO action_event (
                        event_type, event_version,
                        tenant_id, aggregate_type, aggregate_id,
                        payload, actor, actor_type,
                        human_authority,
                        evidence, status,
                        content_hash
                    ) VALUES (
                        :event_type, 1,
                        CAST(:tenant_id AS uuid), 'tenant'::entity_kind,
                        CAST(:aggregate_id AS uuid),
                        CAST(:payload AS jsonb), CAST(:actor AS jsonb), 'human'::actor_type,
                        CAST(:human_authority AS uuid),
                        CAST(:evidence AS jsonb), 'applied'::event_status,
                        :content_hash
                    )
                    """
                ),
                {
                    "event_type": "tenant.switch",
                    "tenant_id": from_tenant_id,
                    "aggregate_id": to_tenant_id,
                    "payload": payload_json,
                    "actor": json.dumps(
                        actor_chain, sort_keys=True, separators=(",", ":")
                    ),
                    "human_authority": (
                        str(human_authority) if human_authority else None
                    ),
                    "evidence": json.dumps(
                        {
                            "source": "switch_workspace",
                            "client_id": claims.get("azp") or claims.get("aud"),
                            "request_id": request.headers.get("x-request-id"),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "content_hash": content_hash,
                },
            )
    except Exception as exc:  # noqa: BLE001 — audit must not break the response
        logger.error(
            "workspace_switch_audit_failed",
            error=str(exc),
            from_tenant_id=from_tenant_id,
            to_tenant_id=to_tenant_id,
        )


async def _emit_onboard_audit_event(
    request: Request,
    *,
    claims: dict[str, Any],
    uc_uid: str,
    tenant_id: str,
    tenant_slug: str,
) -> None:
    """Write a `tenant.onboard` action_event via the audit role.

    Mirrors `_emit_switch_audit_event` / `middleware/audit.py`: a direct INSERT
    into `action_event` under a tenant-bound `contact_ops_audit` session. The
    row's `tenant_id`, `aggregate_id`, and `human_authority` are all the NEWLY
    provisioned workspace / its admin — the caller now owns this tenant, so the
    audit session can satisfy the `ae_audit_insert` WITH CHECK
    (tenant_id = current_tenant_id()). `aggregate_type` is `tenant`.

    Best-effort: any failure is logged and swallowed (audit must never break the
    successful onboard response). As with switch, AuditMiddleware also writes a
    generic `http.POST /api/auth/onboard` event for the request; this
    `tenant.onboard` event marks the SUCCESSFUL provision specifically.
    """
    try:
        actor_chain = getattr(request.state, "actor_chain", {"sub": claims.get("sub")})
        human_authority = (
            getattr(request.state, "human_authority", claims.get("sub")) or uc_uid
        )

        payload: dict[str, Any] = {
            "tenant_id": tenant_id,
            "tenant_slug": tenant_slug,
            "mechanism": "self-serve-onboard",
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(payload_json.encode("utf-8")).digest()

        async with get_audit_db_context(tenant_id=tenant_id, uc_uid=uc_uid) as db:
            await db.execute(
                text(
                    """
                    INSERT INTO action_event (
                        event_type, event_version,
                        tenant_id, aggregate_type, aggregate_id,
                        payload, actor, actor_type,
                        human_authority,
                        evidence, status,
                        content_hash
                    ) VALUES (
                        :event_type, 1,
                        CAST(:tenant_id AS uuid), 'tenant'::entity_kind,
                        CAST(:aggregate_id AS uuid),
                        CAST(:payload AS jsonb), CAST(:actor AS jsonb), 'human'::actor_type,
                        CAST(:human_authority AS uuid),
                        CAST(:evidence AS jsonb), 'applied'::event_status,
                        :content_hash
                    )
                    """
                ),
                {
                    "event_type": "tenant.onboard",
                    "tenant_id": tenant_id,
                    "aggregate_id": tenant_id,
                    "payload": payload_json,
                    "actor": json.dumps(
                        actor_chain, sort_keys=True, separators=(",", ":")
                    ),
                    "human_authority": (
                        str(human_authority) if human_authority else None
                    ),
                    "evidence": json.dumps(
                        {
                            "source": "onboard",
                            "client_id": claims.get("azp") or claims.get("aud"),
                            "request_id": request.headers.get("x-request-id"),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "content_hash": content_hash,
                },
            )
    except Exception as exc:  # noqa: BLE001 — audit must not break the response
        logger.error(
            "onboard_audit_failed",
            error=str(exc),
            tenant_id=tenant_id,
        )
