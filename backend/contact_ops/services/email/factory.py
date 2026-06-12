"""Email provider factory — returns Postmark when configured + enabled, else Noop."""

from __future__ import annotations

from contact_ops.core.config import Settings
from contact_ops.services.email.base import EmailProvider, NoopProvider
from contact_ops.services.email.postmark import PostmarkProvider


def is_configured(settings: Settings) -> bool:
    """True when a Postmark token + a From address are present."""
    return bool(settings.POSTMARK_SERVER_TOKEN and settings.EMAIL_FROM)


def get_email_provider(settings: Settings) -> EmailProvider:
    """Postmark only when configured AND EMAIL_SENDING_ENABLED; else the Noop.

    EMAIL_SENDING_ENABLED is the shadow-first master switch: even with a token
    present, leaving it false keeps sending dormant (log-only), exactly like
    ENTITLEMENT_ENFORCED.
    """
    if settings.EMAIL_SENDING_ENABLED and is_configured(settings):
        return PostmarkProvider(settings)
    return NoopProvider()
