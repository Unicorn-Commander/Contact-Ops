"""OAuth callback routes for contact connectors."""

from __future__ import annotations

import uuid

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from contact_ops.connectors.base import encrypt_payload
from contact_ops.connectors.gmail import exchange_gmail_code
from contact_ops.connectors.m365 import exchange_m365_code
from contact_ops.connectors.oauth_state import consume_state
from contact_ops.core.config import get_settings
from contact_ops.core.database import async_session_maker, bind_session_context

router = APIRouter(tags=["Connectors"])


@router.get("/api/connectors/{provider}/oauth/callback")
async def connector_oauth_callback(
    provider: str,
    # OAuth providers may redirect either with a `code` (success) OR with an
    # `error` (rejection / consent declined / scope refused / etc.) — accept
    # both as optional and surface the rejection to the user instead of 422.
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> RedirectResponse:
    if provider not in {"m365", "gmail"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider")

    settings = get_settings()

    def _redirect_with_error(msg: str) -> RedirectResponse:
        # Provider-specific error path: bounce back to /connectors with the
        # raw message so the frontend's OauthErrorBanner can show it.
        return RedirectResponse(
            f"{settings.FRONTEND_URL}/connectors?error={quote(msg)}&provider={provider}"
        )

    # Provider rejected the auth — surface what they actually said before
    # touching state or attempting any token exchange.
    if error:
        full = f"{error}: {error_description}" if error_description else error
        return _redirect_with_error(full)

    # We expect both code + state on the success path; if either is missing
    # at this point, the provider sent us a malformed callback.
    if not code or not state:
        return _redirect_with_error(
            "missing code or state in OAuth callback — provider redirected without a complete payload"
        )

    try:
        stored = consume_state(provider, state)
        token_payload = (
            await exchange_m365_code(code, stored.code_verifier)
            if provider == "m365"
            else await exchange_gmail_code(code, stored.code_verifier)
        )
    except Exception as exc:
        return _redirect_with_error(str(exc))

    tenant_id = uuid.UUID(stored.tenant_id)
    settings = get_settings()
    # uc_uid source: the signed OAuth `state` we issued at oauth-start time
    # carries the originating user's uc_uid (also stored as configured_by).
    async with async_session_maker() as db:
        await bind_session_context(db, str(tenant_id), stored.uc_uid, settings)
        await db.execute(
            text(
                """
                INSERT INTO connector_configs (
                    tenant_id, provider, display_name, configured_by, encrypted_payload,
                    status, last_error
                ) VALUES (
                    CAST(:tenant_id AS uuid), :provider, :display_name, :configured_by,
                    :encrypted_payload, 'configured', NULL
                )
                ON CONFLICT (tenant_id, provider) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    configured_by = EXCLUDED.configured_by,
                    encrypted_payload = EXCLUDED.encrypted_payload,
                    status = 'configured',
                    last_error = NULL,
                    configured_at = now()
                """
            ),
            {
                "tenant_id": str(tenant_id),
                "provider": provider,
                "display_name": "Microsoft 365" if provider == "m365" else "Gmail",
                "configured_by": stored.uc_uid,
                "encrypted_payload": encrypt_payload(token_payload),
            },
        )
        await db.commit()
    return RedirectResponse(f"{settings.FRONTEND_URL}/connectors?success={provider}")
