"""
Pydantic schemas for Center Deep Data Intel API.

This module exports all schema models for request/response validation.
"""

from app.schemas.catalogue import (
    CatalogueSubmitRequest,
    CompanyCreate,
    ContactCreate,
)
from app.schemas.verify import (
    BatchVerifyRequest,
    BatchVerifyResponse,
    EmailVerifyRequest,
    JobResult,
    JobStatus,
    VerificationResult,
)

__all__ = [
    # Verification schemas
    "EmailVerifyRequest",
    "BatchVerifyRequest",
    "BatchVerifyResponse",
    "VerificationResult",
    "JobStatus",
    "JobResult",
    # Catalogue schemas
    "CompanyCreate",
    "ContactCreate",
    "CatalogueSubmitRequest",
]
