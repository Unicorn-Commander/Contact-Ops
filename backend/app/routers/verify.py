"""
Email verification endpoints for Center Deep Data Intel API.

Provides endpoints for:
- Single email verification
- Batch email verification (sync and async)
- Job status checking for async operations
"""

import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.rbac import Permission, check_permission
from app.core.redis import (
    RedisUnavailableError,
    create_job,
    get_job,
    update_job,
)
from app.core.security import AppContext, get_current_app
from app.core.usage_tracker import check_usage_limit, increment_usage
from app.core.lago_client import get_lago_client
from app.schemas.verify import (
    BatchVerifyRequest,
    BatchVerifyResponse,
    EmailVerifyRequest,
    JobResult,
    JobStatus,
    RiskLevel,
    VerificationResult,
    VerificationStatus,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/verify", tags=["Verification"])


async def perform_verification(
    email: str,
    settings: Settings,
) -> VerificationResult:
    """
    Perform email verification using SMTP checks.

    This is a placeholder implementation. In production, this would:
    1. Check syntax
    2. Check disposable domain lists
    3. Check MX records
    4. Perform SMTP verification

    Args:
        email: Email address to verify
        settings: Application settings

    Returns:
        VerificationResult with verification details
    """
    try:
        # Import email validation
        from email_validator import EmailNotValidError, validate_email

        # Validate email syntax
        try:
            validated = validate_email(email, check_deliverability=False)
            email = validated.normalized
            domain = validated.domain
        except EmailNotValidError as e:
            return VerificationResult(
                email=email,
                status=VerificationStatus.SYNTAX_ERROR,
                is_deliverable=False,
                risk_level=RiskLevel.HIGH,
                error_message=str(e),
            )

        # Check for disposable domains (placeholder list)
        disposable_domains = {
            "tempmail.com", "throwaway.com", "guerrillamail.com",
            "mailinator.com", "10minutemail.com", "fakeinbox.com",
        }
        is_disposable = domain.lower() in disposable_domains

        # Check for role-based addresses
        role_prefixes = {
            "admin", "support", "info", "contact", "sales", "help",
            "noreply", "no-reply", "webmaster", "postmaster", "abuse",
        }
        local_part = email.split("@")[0].lower()
        is_role_based = local_part in role_prefixes

        # Check for free providers
        free_providers = {
            "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
            "aol.com", "icloud.com", "protonmail.com", "mail.com",
        }
        is_free_provider = domain.lower() in free_providers

        # Perform MX lookup
        mx_records = []
        try:
            import dns.resolver

            answers = dns.resolver.resolve(domain, "MX")
            for rdata in answers:
                mx_records.append({
                    "host": str(rdata.exchange).rstrip("."),
                    "priority": rdata.preference,
                })
            mx_records.sort(key=lambda x: x["priority"])
        except Exception as mx_error:
            logger.debug("MX lookup failed", domain=domain, error=str(mx_error))

        # Determine deliverability status
        if is_disposable:
            verification_status = VerificationStatus.DISPOSABLE
            is_deliverable = True  # Technically deliverable, just risky
            risk_level = RiskLevel.HIGH
        elif not mx_records:
            verification_status = VerificationStatus.INVALID
            is_deliverable = False
            risk_level = RiskLevel.HIGH
        else:
            # For now, assume valid if MX records exist
            # Real implementation would do SMTP verification
            verification_status = VerificationStatus.VALID
            is_deliverable = True
            risk_level = RiskLevel.LOW if not is_role_based else RiskLevel.MEDIUM

        # Calculate score
        score = 100
        if is_disposable:
            score -= 50
        if is_role_based:
            score -= 20
        if not mx_records:
            score = 0

        return VerificationResult(
            email=email,
            status=verification_status,
            is_deliverable=is_deliverable,
            is_disposable=is_disposable,
            is_role_based=is_role_based,
            is_catch_all=False,  # Would require SMTP check
            is_free_provider=is_free_provider,
            risk_level=risk_level,
            domain=domain,
            mx_records=[
                {"host": mx["host"], "priority": mx["priority"]}
                for mx in mx_records
            ],
            smtp_details=None,  # Would be populated by SMTP verification
            score=max(0, score),
            verified_at=datetime.utcnow(),
        )

    except Exception as e:
        logger.error("Verification error", email=email, error=str(e))
        return VerificationResult(
            email=email,
            status=VerificationStatus.ERROR,
            is_deliverable=None,
            risk_level=RiskLevel.UNKNOWN,
            error_message=str(e),
        )


@router.post(
    "/email",
    summary="Verify single email",
    description="Verify a single email address and return detailed results.",
    response_model=VerificationResult,
)
async def verify_email(
    request: EmailVerifyRequest,
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VerificationResult:
    """
    Verify a single email address.

    Performs comprehensive email verification including:
    - Syntax validation
    - Domain MX record lookup
    - Disposable email detection
    - Role-based address detection
    - SMTP verification (when enabled)

    Args:
        request: Email verification request
        app: Authenticated application context
        db: Database session
        settings: Application settings

    Returns:
        VerificationResult with detailed verification information
    """
    # Check permission
    check_permission(app.permissions, Permission.VERIFY_SINGLE)

    # Check usage limit (if user has user_id)
    if app.user_id:
        await check_usage_limit(db, app.user_id, "verify:single", app.tier)

    logger.info(
        "Single email verification",
        email=request.email,
        app_name=app.app_name,
    )

    result = await perform_verification(str(request.email), settings)

    # Increment usage after successful verification
    if app.user_id:
        await increment_usage(db, app.user_id, "verify:single")

        # Send billing event to Lago
        lago = get_lago_client()
        verification_id = str(uuid.uuid4())
        await lago.send_verification_event(
            user_id=app.user_id,
            verification_id=verification_id,
            email=str(request.email),
        )

    logger.info(
        "Verification complete",
        email=request.email,
        status=result.status,
        is_deliverable=result.is_deliverable,
    )

    return result


@router.post(
    "/batch",
    summary="Verify batch of emails",
    description="Verify multiple email addresses. Use async=true for large batches.",
    response_model=BatchVerifyResponse,
)
async def verify_batch(
    request: BatchVerifyRequest,
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BatchVerifyResponse:
    """
    Verify a batch of email addresses.

    For small batches (< 100), results are returned immediately.
    For larger batches or when async=true, returns a job_id for polling.

    Args:
        request: Batch verification request
        app: Authenticated application context
        db: Database session
        settings: Application settings

    Returns:
        BatchVerifyResponse with either immediate results or job_id
    """
    # Check permission
    check_permission(app.permissions, Permission.VERIFY_BATCH)

    email_count = len(request.emails)

    # Check usage limit for the entire batch (if user has user_id)
    if app.user_id:
        await check_usage_limit(db, app.user_id, "verify:batch", app.tier, count=email_count)

    logger.info(
        "Batch verification request",
        email_count=email_count,
        async_mode=request.async_mode,
        app_name=app.app_name,
    )

    # Async mode - create job and return ID
    if request.async_mode or email_count > 100:
        job_id = str(uuid.uuid4())

        # Create job record data for Redis storage
        job_data = {
            "job_id": job_id,
            "status": JobStatus.PENDING.value,
            "progress_percent": 0.0,
            "processed": 0,
            "total": email_count,
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "results": None,
            "error_message": None,
            "emails": [str(email) for email in request.emails],
            "user_id": app.user_id,  # Store user_id for usage tracking on completion
        }

        try:
            # Store job in Redis with 24-hour TTL
            await create_job(job_id, job_data)
            logger.info("Created batch job in Redis", job_id=job_id, email_count=email_count)
        except RedisUnavailableError as e:
            logger.error("Failed to create job in Redis", job_id=job_id, error=str(e))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Job storage service temporarily unavailable. Please try again.",
            )

        # Increment usage when job is created (async jobs count immediately)
        if app.user_id:
            await increment_usage(db, app.user_id, "verify:batch", count=email_count)

            # Send billing events to Lago for batch
            lago = get_lago_client()
            for i in range(email_count):
                await lago.send_verification_event(
                    user_id=app.user_id,
                    verification_id=f"{job_id}-{i}",
                    email=str(request.emails[i]) if i < len(request.emails) else None,
                )

        # TODO: Queue to background worker (Celery/ARQ/etc.)
        # For now, the job will be picked up by a worker polling Redis

        return BatchVerifyResponse(job_id=job_id)

    # Synchronous mode - process immediately
    results = []
    lago = get_lago_client()
    batch_id = str(uuid.uuid4())

    for i, email in enumerate(request.emails):
        result = await perform_verification(str(email), settings)
        results.append(result)

        # Send billing event for each verification
        if app.user_id:
            await lago.send_verification_event(
                user_id=app.user_id,
                verification_id=f"{batch_id}-{i}",
                email=str(email),
            )

    # Increment usage after successful verification
    if app.user_id:
        await increment_usage(db, app.user_id, "verify:batch", count=email_count)

    logger.info(
        "Batch verification complete",
        email_count=email_count,
        valid_count=sum(1 for r in results if r.status == VerificationStatus.VALID),
    )

    return BatchVerifyResponse(results=results)


@router.get(
    "/job/{job_id}",
    summary="Get batch job status",
    description="Check the status of an async batch verification job.",
    response_model=JobResult,
)
async def get_job_status(
    job_id: str,
    app: AppContext = Depends(get_current_app),
) -> JobResult:
    """
    Get the status of a batch verification job.

    Returns the current status, progress, and results (if complete).

    Args:
        job_id: The job ID returned from /batch
        app: Authenticated application context

    Returns:
        JobResult with current status and results

    Raises:
        HTTPException: 404 if job not found, 503 if Redis unavailable
    """
    logger.info("Job status request", job_id=job_id, app_name=app.app_name)

    try:
        job_data = await get_job(job_id)
    except RedisUnavailableError as e:
        logger.error("Failed to retrieve job from Redis", job_id=job_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job storage service temporarily unavailable. Please try again.",
        )

    if not job_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    # Parse results if present (they are stored as dicts, need to convert to VerificationResult)
    results = None
    if job_data.get("results"):
        results = [
            VerificationResult(**result_data)
            for result_data in job_data["results"]
        ]

    # Construct JobResult from Redis data
    return JobResult(
        job_id=job_data["job_id"],
        status=JobStatus(job_data["status"]),
        progress_percent=job_data.get("progress_percent", 0.0),
        processed=job_data.get("processed", 0),
        total=job_data["total"],
        results=results,
        created_at=datetime.fromisoformat(job_data["created_at"]) if job_data.get("created_at") else datetime.utcnow(),
        completed_at=datetime.fromisoformat(job_data["completed_at"]) if job_data.get("completed_at") else None,
        error_message=job_data.get("error_message"),
    )
