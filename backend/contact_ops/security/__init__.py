"""Security primitives (SSRF egress guard, etc.)."""

from contact_ops.security.ssrf import (  # noqa: F401
    SSRFBlocked,
    build_ssrf_safe_client,
    check_outbound,
    validate_outbound_url,
)

__all__ = [
    "SSRFBlocked",
    "build_ssrf_safe_client",
    "check_outbound",
    "validate_outbound_url",
]
