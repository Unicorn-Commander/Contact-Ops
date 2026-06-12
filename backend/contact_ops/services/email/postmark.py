"""Postmark transactional email provider.

POST https://api.postmarkapp.com/email/withTemplate with the server token in the
X-Postmark-Server-Token header. NEVER raises and NEVER logs the token: every
failure (transport error, ErrorCode != 0 — e.g. 10 invalid token, 406 inactive
recipient, 422 unsigned From) maps to a SendResult.error so the caller's
originating action is never broken. The recipient is redacted in logs (PII).
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from contact_ops.core.config import Settings
from contact_ops.services.email.base import SendResult, redact_email

logger = structlog.get_logger(__name__)

_POSTMARK_URL = "https://api.postmarkapp.com/email/withTemplate"
_TIMEOUT_SECONDS = 30  # matches the house _KC_TIMEOUT_SECONDS


class PostmarkProvider:
    def __init__(self, settings: Settings) -> None:
        self._token = settings.POSTMARK_SERVER_TOKEN
        self._from = settings.EMAIL_FROM
        self._default_stream = settings.EMAIL_MESSAGE_STREAM

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
        body: dict[str, Any] = {
            "From": self._from,
            "To": to,
            "TemplateAlias": template_alias,
            "TemplateModel": model,
            "MessageStream": message_stream or self._default_stream,
        }
        if tag:
            body["Tag"] = tag
        if metadata:
            body["Metadata"] = metadata

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    _POSTMARK_URL,
                    json=body,
                    headers={
                        "X-Postmark-Server-Token": self._token,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
            data = resp.json() if resp.content else {}
        except Exception as exc:  # noqa: BLE001 — email send must never raise
            logger.warning(
                "email_send_errored",
                template=template_alias,
                to=redact_email(to),
                error=str(exc),
            )
            return SendResult(ok=False, error=str(exc))

        error_code = data.get("ErrorCode", 0 if resp.status_code == 200 else resp.status_code)
        if resp.status_code == 200 and error_code == 0:
            return SendResult(ok=True, message_id=data.get("MessageID"))

        # ErrorCode != 0 is terminal for this attempt (406 inactive recipient is a
        # suppression, not transient — don't retry). Log + return, never raise.
        logger.warning(
            "email_send_failed",
            template=template_alias,
            to=redact_email(to),
            error_code=error_code,
            message=data.get("Message"),
        )
        return SendResult(ok=False, error_code=error_code, error=data.get("Message"))
