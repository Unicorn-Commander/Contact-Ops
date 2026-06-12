"""App-generated email notifications — typed, best-effort helpers.

One function per email kind. Each builds the Postmark TemplateModel (incl. app
links off settings.FRONTEND_URL), picks a Tag, and calls the provider. Every send
is best-effort: failures are swallowed to a structured warning so a flaky/
unconfigured Postmark NEVER fails the originating MCP/HTTP call. Dormant by
default — get_email_provider returns the Noop until Postmark is configured + on.
"""

from __future__ import annotations

import structlog

from contact_ops.core.config import Settings, get_settings
from contact_ops.services.email.base import EmailTemplate, SendResult
from contact_ops.services.email.factory import get_email_provider

logger = structlog.get_logger(__name__)


async def send_member_invite(
    *,
    to_email: str,
    workspace_name: str,
    role: str,
    inviter: str,
    settings: Settings | None = None,
) -> SendResult:
    """Notify an existing user they were added to a workspace."""
    settings = settings or get_settings()
    model = {
        "workspace_name": workspace_name,
        "role": role,
        "inviter": inviter,
        "action_url": settings.FRONTEND_URL,
        "app_url": settings.FRONTEND_URL,
    }
    try:
        return await get_email_provider(settings).send_template(
            to=to_email,
            template_alias=EmailTemplate.MEMBER_INVITE,
            model=model,
            tag="member-invite",
        )
    except Exception as exc:  # noqa: BLE001 — notifications never break the caller
        logger.warning("notification_failed", kind="member_invite", error=str(exc))
        return SendResult(ok=False, error=str(exc))


async def send_member_invite_pending(
    *,
    to_email: str,
    workspace_name: str,
    role: str,
    inviter: str,
    signup_url: str | None = None,
    settings: Settings | None = None,
) -> SendResult:
    """Invite a not-yet-registered teammate with a sign-in deep link.

    Wired but only reachable once EMAIL_INVITES_ALLOW_PENDING + the pending-
    invitations table land with the Signup milestone.
    """
    settings = settings or get_settings()
    model = {
        "workspace_name": workspace_name,
        "role": role,
        "inviter": inviter,
        "signup_url": signup_url or settings.FRONTEND_URL,
        "app_url": settings.FRONTEND_URL,
    }
    try:
        return await get_email_provider(settings).send_template(
            to=to_email,
            template_alias=EmailTemplate.MEMBER_INVITE_PENDING,
            model=model,
            tag="member-invite-pending",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("notification_failed", kind="member_invite_pending", error=str(exc))
        return SendResult(ok=False, error=str(exc))
