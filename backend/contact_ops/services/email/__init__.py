"""Provider-pluggable transactional email (dormant until Postmark is configured)."""

from contact_ops.services.email.base import (  # noqa: F401
    EmailProvider,
    EmailTemplate,
    NoopProvider,
    SendResult,
    redact_email,
)
from contact_ops.services.email.factory import (  # noqa: F401
    get_email_provider,
    is_configured,
)

__all__ = [
    "EmailProvider",
    "EmailTemplate",
    "NoopProvider",
    "SendResult",
    "get_email_provider",
    "is_configured",
    "redact_email",
]
