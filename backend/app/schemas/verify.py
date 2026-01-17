"""
Pydantic schemas for email verification endpoints.

These schemas define the request/response models for the verification API,
matching the output format of the smtp_verifier service.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class VerificationStatus(str, Enum):
    """Possible email verification statuses."""

    VALID = "valid"
    INVALID = "invalid"
    CATCH_ALL = "catch_all"
    UNKNOWN = "unknown"
    DISPOSABLE = "disposable"
    ROLE_BASED = "role_based"
    SYNTAX_ERROR = "syntax_error"
    TIMEOUT = "timeout"
    ERROR = "error"


class RiskLevel(str, Enum):
    """Risk level for email deliverability."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class EmailVerifyRequest(BaseModel):
    """Request schema for single email verification."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "email": "REMOVED"
            }
        },
    )

    email: EmailStr = Field(
        ...,
        description="Email address to verify",
    )


class BatchVerifyRequest(BaseModel):
    """Request schema for batch email verification."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "emails": ["REMOVED", "REMOVED"],
                "async_mode": False,
            }
        },
    )

    emails: list[EmailStr] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="List of email addresses to verify (max 1000)",
    )
    async_mode: bool = Field(
        default=False,
        alias="async",
        description="If true, return job_id for async processing",
    )


class SMTPDetails(BaseModel):
    """SMTP verification details from the verifier."""

    model_config = ConfigDict(extra="allow")

    host: str | None = Field(default=None, description="SMTP host used for verification")
    port: int | None = Field(default=None, description="SMTP port used")
    response_code: int | None = Field(default=None, description="SMTP response code")
    response_message: str | None = Field(default=None, description="SMTP response message")
    connection_time_ms: float | None = Field(default=None, description="Connection time in milliseconds")
    verification_time_ms: float | None = Field(default=None, description="Total verification time")


class MXRecord(BaseModel):
    """MX record information."""

    host: str = Field(..., description="MX hostname")
    priority: int = Field(..., description="MX priority")


class VerificationResult(BaseModel):
    """
    Result of email verification.

    Matches the output format from the smtp_verifier service.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "REMOVED",
                "status": "valid",
                "is_deliverable": True,
                "is_disposable": False,
                "is_role_based": False,
                "is_catch_all": False,
                "is_free_provider": False,
                "risk_level": "low",
                "domain": "example.com",
                "mx_records": [{"host": "mx1.example.com", "priority": 10}],
                "smtp_details": {"response_code": 250},
                "verified_at": "2024-01-15T10:30:00Z",
            }
        },
    )

    # Core result
    email: str = Field(..., description="The email address that was verified")
    status: VerificationStatus = Field(..., description="Verification status")

    # Deliverability flags
    is_deliverable: bool | None = Field(
        default=None,
        description="Whether the email is likely deliverable",
    )
    is_disposable: bool = Field(
        default=False,
        description="Whether the domain is a known disposable email provider",
    )
    is_role_based: bool = Field(
        default=False,
        description="Whether this is a role-based address (admin@, support@, etc.)",
    )
    is_catch_all: bool = Field(
        default=False,
        description="Whether the domain accepts all addresses (catch-all)",
    )
    is_free_provider: bool = Field(
        default=False,
        description="Whether the domain is a free email provider",
    )

    # Risk assessment
    risk_level: RiskLevel = Field(
        default=RiskLevel.UNKNOWN,
        description="Risk level for deliverability",
    )

    # Domain information
    domain: str | None = Field(default=None, description="The email domain")
    mx_records: list[MXRecord] = Field(
        default_factory=list,
        description="MX records for the domain",
    )

    # SMTP verification details
    smtp_details: SMTPDetails | None = Field(
        default=None,
        description="Detailed SMTP verification information",
    )

    # Metadata
    verified_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the verification was performed",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if verification failed",
    )

    # Score (0-100)
    score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Deliverability score (0-100)",
    )


class JobStatus(str, Enum):
    """Status of a batch verification job."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobResult(BaseModel):
    """Result of a batch verification job."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "processing",
                "progress_percent": 45.5,
                "processed": 455,
                "total": 1000,
                "results": None,
                "created_at": "2024-01-15T10:30:00Z",
                "completed_at": None,
            }
        },
    )

    job_id: str = Field(..., description="Unique job identifier")
    status: JobStatus = Field(..., description="Current job status")
    progress_percent: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Completion percentage",
    )
    processed: int = Field(default=0, ge=0, description="Number of emails processed")
    total: int = Field(..., ge=0, description="Total emails in batch")
    results: list[VerificationResult] | None = Field(
        default=None,
        description="Verification results (only present when completed)",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the job was created",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When the job completed",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if job failed",
    )


class BatchVerifyResponse(BaseModel):
    """Response for batch verification request."""

    job_id: str | None = Field(
        default=None,
        description="Job ID for async requests",
    )
    results: list[VerificationResult] | None = Field(
        default=None,
        description="Immediate results for sync requests",
    )
