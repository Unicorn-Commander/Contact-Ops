"""
Center Deep Data Intel - Services Package.

This package contains the core service modules for email verification
and data processing.
"""

from .smtp_verifier import (
    # Data classes
    VerificationResult,
    VerificationConfig,

    # Main verification functions
    verify_email,
    verify_email_async,

    # Batch verification
    verify_emails_batch,
    verify_emails_batch_async,

    # Quick checks
    quick_check,
    quick_check_async,
    is_valid_email,

    # Utility functions
    validate_email_syntax,
    is_role_based_email,
    get_mx_records,
    calculate_risk_score,
)

from .disposable_domains import (
    is_disposable_domain,
    get_disposable_domains,
    get_disposable_domain_count,
    DISPOSABLE_DOMAINS,
)

__all__ = [
    # SMTP Verifier
    "VerificationResult",
    "VerificationConfig",
    "verify_email",
    "verify_email_async",
    "verify_emails_batch",
    "verify_emails_batch_async",
    "quick_check",
    "quick_check_async",
    "is_valid_email",
    "validate_email_syntax",
    "is_role_based_email",
    "get_mx_records",
    "calculate_risk_score",

    # Disposable Domains
    "is_disposable_domain",
    "get_disposable_domains",
    "get_disposable_domain_count",
    "DISPOSABLE_DOMAINS",
]
