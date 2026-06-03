"""Signed read-only workspace mirror endpoint (SUITE-IDENTITY §4).

`GET /api/v1/workspaces/{workspace_id}` is the server-to-server mirror-sync READ
surface sibling suite apps use to lazy-mirror CO's `public.tenants` projection
(SUITE-IDENTITY §4: "lazy-mirror via a signed GET {registry}/workspaces/{id}").
It is NOT a user surface and is NOT reached via the JWT middleware: the path is
in JWTValidationMiddleware.SKIP_PREFIXES and the endpoint authenticates itself
inline by an HMAC-SHA256 signature (the same "carry your own inline auth"
discipline the CardDAV / oauth-callback bypasses use).

Contract:
  * Returns EXACTLY the thin projection (tenancy.workspace_thin_projection /
    WORKSPACE_THIN_FIELDS) — 9 allow-listed fields and NEVER an infra column
    (graph_mode/graph_name/qdrant_namespace/garage_bucket_prefix/tenant_keys/
    owner_user_id/retention_policy/branding).
  * INERT (503) when WORKSPACE_MIRROR_READ_SECRET is unset, so deploying this
    code before the secret is provisioned is safe (mirrors the switch-client
    inert guard).
  * Authenticated: a caller signs HMAC-SHA256 over "{workspace_id}.{unix_ts}"
    with the shared secret and presents `X-Workspace-Signature: {ts}.{hex}`.
    A missing/old/forged signature -> 401, NEVER the body — so the endpoint is
    not a workspace-existence oracle (a non-member/unauthenticated caller cannot
    learn whether a workspace exists). A missing workspace with a VALID signature
    -> 404 (the caller is a trusted sibling; existence is not secret to it).

This is the §2.2 deliverable. The exact signed-read mechanism (this shared-secret
HMAC vs. a Brigade service token, and signed-request vs. signed-response) is a
coordinate-with-ops-center decision; shared-secret HMAC is implemented here as
the simplest authenticated server-to-server read that ships dark until the secret
exists. See the hand-off §2.2 "coordinate / do not guess".
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy import select

from contact_ops.core.config import Settings, get_settings
from contact_ops.core.database import async_session_maker, bind_session_context
from contact_ops.mcp.tools.tenancy import workspace_thin_projection
from contact_ops.models import Tenant

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Workspaces"])


def _verify_mirror_signature(
    workspace_id: str, signature_header: str | None, settings: Settings
) -> bool:
    """Verify the `X-Workspace-Signature: {ts}.{hex}` HMAC over
    "{workspace_id}.{ts}".

    Constant-time compare; rejects a timestamp outside
    WORKSPACE_MIRROR_READ_MAX_AGE_SECONDS (replay window). Fail-closed on any
    malformed input. The secret is assumed non-empty (the caller checks the inert
    guard first).
    """
    secret = settings.WORKSPACE_MIRROR_READ_SECRET
    if not secret or not signature_header:
        return False
    try:
        ts_str, provided_hex = signature_header.split(".", 1)
        ts = int(ts_str)
    except (ValueError, AttributeError):
        return False
    # Replay window: reject signatures too far from now (either direction).
    if abs(int(time.time()) - ts) > settings.WORKSPACE_MIRROR_READ_MAX_AGE_SECONDS:
        return False
    expected = hmac.new(
        secret.encode(),
        f"{workspace_id}.{ts}".encode(),
        hashlib.sha256,
    ).hexdigest()
    # Constant-time compare to avoid leaking the signature byte-by-byte.
    return hmac.compare_digest(expected, provided_hex.strip())


@router.get(
    "/workspaces/{workspace_id}",
    summary="Signed read-only thin workspace projection (server-to-server mirror)",
)
async def get_workspace_projection(
    workspace_id: uuid.UUID,
    x_workspace_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return the suite-shared THIN projection for ``workspace_id`` (signed read).

    See module docstring for the contract. INERT-503 without the secret; 401 on a
    missing/forged/expired signature (no existence oracle); 404 on a missing
    workspace WITH a valid signature.
    """
    settings = get_settings()

    # Inert-until-configured (mirrors the switch-client guard).
    if not settings.WORKSPACE_MIRROR_READ_SECRET:
        logger.warning("workspace_mirror_read_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="workspace mirror read not configured",
        )

    # Inline auth. Bad/missing/expired signature -> 401, NEVER the body, so this
    # is not a workspace-existence oracle.
    if not _verify_mirror_signature(
        str(workspace_id), x_workspace_signature, settings
    ):
        logger.warning(
            "workspace_mirror_read_signature_invalid", workspace_id=str(workspace_id)
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid workspace signature",
        )

    # Authenticated trusted-sibling read: serve the thin projection (or 404).
    # We bind app.tenant_id = workspace_id so the tenants_select RLS branch
    # `id = current_tenant_id()` matches EXACTLY this one row (with no GUC bound,
    # current_tenant_id() is NULL and the by-id select would return nothing —
    # 0034's NULL-over-deny trap). Binding to the requested id is safe: the caller
    # already proved authorization via the HMAC signature, and the GUC scopes
    # visibility to precisely the requested workspace, nothing wider. uc_uid is a
    # service identity for attribution parity. The projection exposes ONLY
    # non-infra fields regardless.
    async with async_session_maker() as session:
        await bind_session_context(
            session, str(workspace_id), "service:workspace-mirror-read", settings
        )
        tenant = await session.scalar(
            select(Tenant).where(
                Tenant.id == workspace_id, Tenant.deprecated_at.is_(None)
            )
        )
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workspace not found",
        )
    return workspace_thin_projection(tenant)
