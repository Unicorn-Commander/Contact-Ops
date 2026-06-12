"""Email provider contract + the dormant Noop default.

Scope is APP-generated transactional mail (member invites, GDPR/DSR notices) —
account verification + password reset are Keycloak's job (realm uchub) and must
NOT be re-implemented here. Mirrors keycloak_admin's inert-when-unconfigured
posture: the Noop provider logs `email_would_send` and returns success without
any network call, so the whole path is dormant until Postmark is configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)


class EmailTemplate:
    """Postmark template aliases (the names stored on the Postmark server)."""

    MEMBER_INVITE = "member-invite"
    MEMBER_INVITE_PENDING = "member-invite-pending"
    GDPR_DSR_ACK = "gdpr-dsr-acknowledgement"
    DATA_RESIDENCY_NOTICE = "data-residency-notice"


@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: str | None = None
    error_code: int | None = None
    error: str | None = None


def redact_email(addr: str) -> str:
    """Redact a recipient for logs (PII): ``a***@example.com``."""
    local, sep, domain = addr.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"


@runtime_checkable
class EmailProvider(Protocol):
    async def send_template(
        self,
        *,
        to: str,
        template_alias: str,
        model: dict[str, Any],
        tag: str | None = None,
        metadata: dict[str, str] | None = None,
        message_stream: str | None = None,
    ) -> SendResult: ...


class NoopProvider:
    """Dormant default — logs the intended send (redacted), never calls out."""

    async def send_template(
        self,
        *,
        to: str,
        template_alias: str,
        model: dict[str, Any],
        tag: str | None = None,
        metadata: dict[str, str] | None = None,
        message_stream: str | None = None,
    ) -> SendResult:
        logger.info(
            "email_would_send",
            template=template_alias,
            to=redact_email(to),
            tag=tag,
            stream=message_stream or "outbound",
        )
        return SendResult(ok=True, message_id=None)
