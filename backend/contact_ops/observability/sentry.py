"""Sentry error tracking — DORMANT until SENTRY_DSN is set.

Mirrors the dormant pattern used across Contact-Ops (entitlement, OTel): with no
DSN configured the whole path no-ops, so the SDK adds zero behavior in prod
until someone deliberately points it at a Sentry project. When enabled:

- send_default_pii is HARDCODED False (not a flag). This is multi-tenant contact
  PII under RLS; Sentry's PII mode would attach request bodies/headers/IPs — a
  cross-tenant leak and a tenants.gdpr_mode problem. A before_send scrubber also
  strips auth headers as defence-in-depth.
- APM tracing + profiling default OFF (0.0 sample) until raised via settings.
- Errors are tagged with tenant_id/uc_uid by the request middleware so they're
  attributable without exposing the underlying contact data.

Idempotent (mirrors otel.py's _INITIALIZED guard) so a reload can't double-init.
"""

from __future__ import annotations

from typing import Any

import structlog

from contact_ops.core.config import Settings

logger = structlog.get_logger(__name__)

_INITIALIZED = False

# Header keys scrubbed from any event before it leaves the process.
_SENSITIVE_HEADERS = frozenset(
    {"authorization", "cookie", "set-cookie", "x-api-key", "x-request-id"}
)


def _before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Drop sensitive request headers as defence-in-depth (PII is already off)."""
    try:
        headers = event.get("request", {}).get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() in _SENSITIVE_HEADERS:
                    headers[key] = "[scrubbed]"
    except Exception:  # noqa: BLE001 — scrubbing must never break error reporting
        pass
    return event


def init_sentry(settings: Settings) -> bool:
    """Initialise Sentry if SENTRY_DSN is set. Returns True iff active.

    No-op (returns False) when the DSN is empty or the SDK isn't installed.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return True
    if not settings.SENTRY_DSN:
        logger.info("sentry_dormant", reason="SENTRY_DSN unset")
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("sentry_sdk_missing", reason="sentry-sdk not installed")
        return False

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENV,
        release=f"contact-ops@{settings.APP_VERSION}",
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        send_default_pii=False,  # NEVER true — multi-tenant contact PII
        integrations=[StarletteIntegration(), FastApiIntegration()],
        before_send=_before_send,
    )
    _INITIALIZED = True
    logger.info("sentry_initialized", environment=settings.ENV)
    return True


def tag_request_scope(tenant_id: str | None, uc_uid: str | None) -> None:
    """Attach tenant_id/uc_uid tags to the active Sentry scope (no-op if off)."""
    if not _INITIALIZED:
        return
    try:
        import sentry_sdk

        if tenant_id:
            sentry_sdk.set_tag("tenant_id", tenant_id)
        if uc_uid:
            sentry_sdk.set_tag("uc_uid", uc_uid)
    except Exception:  # noqa: BLE001 — tagging must never break the request
        pass
