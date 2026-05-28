"""OAuth callback routes for contact connectors."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from contact_ops.connectors.base import encrypt_payload
from contact_ops.connectors.gmail import exchange_gmail_code
from contact_ops.connectors.m365 import exchange_m365_code
from contact_ops.connectors.oauth_state import consume_state
from contact_ops.core.config import get_settings
from contact_ops.core.database import async_session_maker

router = APIRouter(tags=["Connectors"])


@router.get("/api/connectors/{provider}/oauth/callback")
async def connector_oauth_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    if provider not in {"m365", "gmail"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider")
    try:
        stored = consume_state(provider, state)
        token_payload = (
            await exchange_m365_code(code, stored.code_verifier)
            if provider == "m365"
            else await exchange_gmail_code(code, stored.code_verifier)
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    tenant_id = uuid.UUID(stored.tenant_id)
    settings = get_settings()
    async with async_session_maker() as db:
        await db.execute(
            text("SELECT set_config(:k, :v, true)"),
            {"k": settings.TENANT_GUC_NAME, "v": str(tenant_id)},
        )
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
