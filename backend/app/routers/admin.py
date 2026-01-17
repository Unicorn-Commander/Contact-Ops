"""
Admin API router for Center Deep Data Intel.

Provides administrative endpoints for Ops-Center integration:
- Dashboard stats and metrics
- API key management
- Activity logs
- Settings configuration

These endpoints are designed to be consumed by Ops-Center's admin panel.
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.api_key import ApiKey
from app.models.company import Company
from app.models.contact import Contact
from app.models.email import Email
from app.models.job import Job
from app.models.source import Source

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


# =============================================================================
# Pydantic Models
# =============================================================================

class DashboardStats(BaseModel):
    """Overall dashboard statistics."""
    total_companies: int = 0
    total_contacts: int = 0
    total_emails: int = 0
    verified_emails: int = 0
    valid_emails: int = 0
    invalid_emails: int = 0
    disposable_emails: int = 0
    verification_jobs: int = 0
    pending_jobs: int = 0
    source_apps_count: int = 0
    api_keys_count: int = 0
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class SourceAppStats(BaseModel):
    """Statistics per source application."""
    source_app: str
    companies: int = 0
    contacts: int = 0
    emails: int = 0
    last_activity: Optional[datetime] = None


class TimeSeriesPoint(BaseModel):
    """Single point in time series data."""
    date: str
    count: int


class ActivityStats(BaseModel):
    """Activity statistics over time."""
    verifications_today: int = 0
    verifications_week: int = 0
    verifications_month: int = 0
    submissions_today: int = 0
    submissions_week: int = 0
    submissions_month: int = 0
    daily_verifications: list[TimeSeriesPoint] = []
    daily_submissions: list[TimeSeriesPoint] = []


class RecentActivity(BaseModel):
    """Recent activity log entry."""
    id: str
    type: str  # verification, submission, api_key_created, etc.
    description: str
    source_app: Optional[str] = None
    timestamp: datetime
    details: dict[str, Any] = {}


class ApiKeyInfo(BaseModel):
    """API key information (without the actual key)."""
    id: UUID
    key_prefix: str
    app_name: str
    description: Optional[str] = None
    tier: str = "standard"
    rate_limit: int = 1000
    is_active: bool = True
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    request_count: int = 0
    created_at: datetime


class ApiKeyCreate(BaseModel):
    """Request to create a new API key."""
    app_name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    tier: str = Field("standard", pattern="^(standard|premium|unlimited)$")
    rate_limit: int = Field(1000, ge=1, le=1000000)
    expires_days: Optional[int] = Field(None, ge=1, le=365)


class ApiKeyCreated(BaseModel):
    """Response when API key is created (includes the actual key once)."""
    id: UUID
    key: str  # The actual API key - only shown once!
    key_prefix: str
    app_name: str
    tier: str
    rate_limit: int
    expires_at: Optional[datetime] = None
    created_at: datetime
    warning: str = "Save this key now. It cannot be retrieved again."


class AppSettings(BaseModel):
    """Application settings."""
    smtp_timeout: int = Field(10, ge=1, le=60)
    smtp_concurrency: int = Field(10, ge=1, le=100)
    max_batch_size: int = Field(1000, ge=1, le=10000)
    rate_limit_default: int = Field(1000, ge=1, le=100000)
    catch_all_detection: bool = True
    disposable_detection: bool = True
    role_based_detection: bool = True


# =============================================================================
# Dashboard & Stats Endpoints
# =============================================================================

@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db)
) -> DashboardStats:
    """
    Get comprehensive dashboard statistics.

    Returns counts of companies, contacts, emails, verification status,
    and other key metrics for the admin dashboard.
    """
    try:
        # Get counts from each table
        companies_count = await db.scalar(select(func.count(Company.id)))
        contacts_count = await db.scalar(select(func.count(Contact.id)))
        emails_count = await db.scalar(select(func.count(Email.id)))

        # Email verification stats
        verified_count = await db.scalar(
            select(func.count(Email.id))
            .where(Email.is_verified_smtp == True)
        )
        valid_count = await db.scalar(
            select(func.count(Email.id))
            .where(Email.is_valid == True)
        )
        invalid_count = await db.scalar(
            select(func.count(Email.id))
            .where(Email.is_valid == False)
        )
        disposable_count = await db.scalar(
            select(func.count(Email.id))
            .where(Email.is_disposable == True)
        )

        # Job stats
        jobs_count = await db.scalar(select(func.count(Job.id)))
        pending_jobs = await db.scalar(
            select(func.count(Job.id))
            .where(Job.status.in_(["queued", "processing"]))
        )

        # Source apps count
        source_apps = await db.scalar(
            select(func.count(func.distinct(Source.source_app)))
        )

        # API keys count
        api_keys_count = await db.scalar(
            select(func.count(ApiKey.id))
            .where(ApiKey.is_active == True)
        )

        return DashboardStats(
            total_companies=companies_count or 0,
            total_contacts=contacts_count or 0,
            total_emails=emails_count or 0,
            verified_emails=verified_count or 0,
            valid_emails=valid_count or 0,
            invalid_emails=invalid_count or 0,
            disposable_emails=disposable_count or 0,
            verification_jobs=jobs_count or 0,
            pending_jobs=pending_jobs or 0,
            source_apps_count=source_apps or 0,
            api_keys_count=api_keys_count or 0,
            last_updated=datetime.utcnow(),
        )

    except Exception as e:
        logger.error("Failed to get dashboard stats", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


@router.get("/stats/by-source", response_model=list[SourceAppStats])
async def get_stats_by_source(
    db: AsyncSession = Depends(get_db)
) -> list[SourceAppStats]:
    """
    Get statistics broken down by source application.

    Shows how much data each integrated app has contributed.
    """
    try:
        # Query source stats using raw SQL for complex aggregation
        query = text("""
            SELECT
                source_app,
                COUNT(DISTINCT CASE WHEN entity_type = 'company' THEN entity_id END) as companies,
                COUNT(DISTINCT CASE WHEN entity_type = 'contact' THEN entity_id END) as contacts,
                COUNT(DISTINCT CASE WHEN entity_type = 'email' THEN entity_id END) as emails,
                MAX(created_at) as last_activity
            FROM catalogue_sources
            GROUP BY source_app
            ORDER BY last_activity DESC
        """)

        result = await db.execute(query)
        rows = result.fetchall()

        return [
            SourceAppStats(
                source_app=row.source_app,
                companies=row.companies or 0,
                contacts=row.contacts or 0,
                emails=row.emails or 0,
                last_activity=row.last_activity,
            )
            for row in rows
        ]

    except Exception as e:
        logger.error("Failed to get source stats", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get source stats: {str(e)}")


@router.get("/activity", response_model=ActivityStats)
async def get_activity_stats(
    db: AsyncSession = Depends(get_db)
) -> ActivityStats:
    """
    Get activity statistics and time series data.

    Returns verification and submission counts for today, this week,
    and this month, plus daily time series for charting.
    """
    try:
        now = datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        # Verification counts (from emails table based on last_verified_at)
        verifications_today = await db.scalar(
            select(func.count(Email.id))
            .where(Email.last_verified_at >= today)
        ) or 0

        verifications_week = await db.scalar(
            select(func.count(Email.id))
            .where(Email.last_verified_at >= week_ago)
        ) or 0

        verifications_month = await db.scalar(
            select(func.count(Email.id))
            .where(Email.last_verified_at >= month_ago)
        ) or 0

        # Submission counts (from sources table)
        submissions_today = await db.scalar(
            select(func.count(Source.id))
            .where(Source.created_at >= today)
        ) or 0

        submissions_week = await db.scalar(
            select(func.count(Source.id))
            .where(Source.created_at >= week_ago)
        ) or 0

        submissions_month = await db.scalar(
            select(func.count(Source.id))
            .where(Source.created_at >= month_ago)
        ) or 0

        # Daily time series for last 30 days
        daily_query = text("""
            SELECT DATE(last_verified_at) as date, COUNT(*) as count
            FROM catalogue_emails
            WHERE last_verified_at >= :start_date
            GROUP BY DATE(last_verified_at)
            ORDER BY date
        """)

        result = await db.execute(daily_query, {"start_date": month_ago})
        daily_verifications = [
            TimeSeriesPoint(date=str(row.date), count=row.count)
            for row in result.fetchall()
        ]

        submissions_query = text("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM catalogue_sources
            WHERE created_at >= :start_date
            GROUP BY DATE(created_at)
            ORDER BY date
        """)

        result = await db.execute(submissions_query, {"start_date": month_ago})
        daily_submissions = [
            TimeSeriesPoint(date=str(row.date), count=row.count)
            for row in result.fetchall()
        ]

        return ActivityStats(
            verifications_today=verifications_today,
            verifications_week=verifications_week,
            verifications_month=verifications_month,
            submissions_today=submissions_today,
            submissions_week=submissions_week,
            submissions_month=submissions_month,
            daily_verifications=daily_verifications,
            daily_submissions=daily_submissions,
        )

    except Exception as e:
        logger.error("Failed to get activity stats", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get activity stats: {str(e)}")


@router.get("/activity/recent", response_model=list[RecentActivity])
async def get_recent_activity(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db)
) -> list[RecentActivity]:
    """
    Get recent activity log.

    Returns the most recent verifications, submissions, and other activities.
    """
    try:
        activities = []

        # Recent email verifications
        recent_emails = await db.execute(
            select(Email)
            .where(Email.last_verified_at.isnot(None))
            .order_by(Email.last_verified_at.desc())
            .limit(limit // 2)
        )

        for email in recent_emails.scalars():
            status = "valid" if email.is_valid else "invalid"
            activities.append(RecentActivity(
                id=str(email.id),
                type="verification",
                description=f"Verified {email.email} - {status}",
                source_app=email.discovered_by_app,
                timestamp=email.last_verified_at,
                details={
                    "email": email.email,
                    "is_valid": email.is_valid,
                    "is_disposable": email.is_disposable,
                    "risk_score": float(email.risk_score) if email.risk_score else None,
                }
            ))

        # Recent source submissions
        recent_sources = await db.execute(
            select(Source)
            .order_by(Source.created_at.desc())
            .limit(limit // 2)
        )

        for source in recent_sources.scalars():
            activities.append(RecentActivity(
                id=str(source.id),
                type="submission",
                description=f"Data submitted by {source.source_app} ({source.entity_type})",
                source_app=source.source_app,
                timestamp=source.created_at,
                details={
                    "entity_type": source.entity_type,
                    "entity_id": str(source.entity_id),
                }
            ))

        # Sort by timestamp and limit
        activities.sort(key=lambda x: x.timestamp, reverse=True)
        return activities[:limit]

    except Exception as e:
        logger.error("Failed to get recent activity", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get activity: {str(e)}")


# =============================================================================
# API Key Management Endpoints
# =============================================================================

@router.get("/api-keys", response_model=list[ApiKeyInfo])
async def list_api_keys(
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db)
) -> list[ApiKeyInfo]:
    """
    List all API keys.

    Returns API key metadata (not the actual keys).
    """
    try:
        query = select(ApiKey).order_by(ApiKey.created_at.desc())

        if not include_inactive:
            query = query.where(ApiKey.is_active == True)

        result = await db.execute(query)
        keys = result.scalars().all()

        return [
            ApiKeyInfo(
                id=key.id,
                key_prefix=key.key_prefix,
                app_name=key.app_name,
                description=key.description,
                tier=key.tier,
                rate_limit=key.rate_limit,
                is_active=key.is_active,
                expires_at=key.expires_at,
                last_used_at=key.last_used_at,
                request_count=key.request_count,
                created_at=key.created_at,
            )
            for key in keys
        ]

    except Exception as e:
        logger.error("Failed to list API keys", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to list API keys: {str(e)}")


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(
    request: ApiKeyCreate,
    db: AsyncSession = Depends(get_db)
) -> ApiKeyCreated:
    """
    Create a new API key.

    Returns the full API key ONCE. It cannot be retrieved again.
    Store it securely!
    """
    try:
        # Generate a secure API key
        # Format: sk-{app_prefix}-{random_32_chars}
        app_prefix = request.app_name.lower().replace(" ", "-").replace("_", "-")[:10]
        random_part = secrets.token_urlsafe(24)  # 32 chars base64
        api_key = f"sk-{app_prefix}-{random_part}"

        # Hash the key for storage
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        key_prefix = f"sk-{app_prefix}"

        # Calculate expiration
        expires_at = None
        if request.expires_days:
            expires_at = datetime.utcnow() + timedelta(days=request.expires_days)

        # Create the API key record
        new_key = ApiKey(
            key_prefix=key_prefix,
            key_hash=key_hash,
            app_name=request.app_name,
            description=request.description,
            tier=request.tier,
            rate_limit=request.rate_limit,
            expires_at=expires_at,
        )

        db.add(new_key)
        await db.flush()  # Get the ID

        logger.info(
            "API key created",
            app_name=request.app_name,
            key_prefix=key_prefix,
            tier=request.tier,
        )

        return ApiKeyCreated(
            id=new_key.id,
            key=api_key,  # Only time the full key is returned!
            key_prefix=key_prefix,
            app_name=request.app_name,
            tier=request.tier,
            rate_limit=request.rate_limit,
            expires_at=expires_at,
            created_at=new_key.created_at,
        )

    except Exception as e:
        logger.error("Failed to create API key", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to create API key: {str(e)}")


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """
    Revoke (deactivate) an API key.

    The key will no longer work for authentication.
    """
    try:
        result = await db.execute(
            select(ApiKey).where(ApiKey.id == key_id)
        )
        key = result.scalar_one_or_none()

        if not key:
            raise HTTPException(status_code=404, detail="API key not found")

        if not key.is_active:
            raise HTTPException(status_code=400, detail="API key is already revoked")

        key.is_active = False

        logger.info(
            "API key revoked",
            key_id=str(key_id),
            app_name=key.app_name,
        )

        return {
            "success": True,
            "message": f"API key for {key.app_name} has been revoked",
            "key_id": str(key_id),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to revoke API key", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to revoke API key: {str(e)}")


@router.post("/api-keys/{key_id}/regenerate", response_model=ApiKeyCreated)
async def regenerate_api_key(
    key_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> ApiKeyCreated:
    """
    Regenerate an API key.

    Creates a new key for the same app, revokes the old one.
    Returns the new key ONCE.
    """
    try:
        result = await db.execute(
            select(ApiKey).where(ApiKey.id == key_id)
        )
        old_key = result.scalar_one_or_none()

        if not old_key:
            raise HTTPException(status_code=404, detail="API key not found")

        # Revoke old key
        old_key.is_active = False

        # Generate new key
        app_prefix = old_key.app_name.lower().replace(" ", "-").replace("_", "-")[:10]
        random_part = secrets.token_urlsafe(24)
        api_key = f"sk-{app_prefix}-{random_part}"

        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        key_prefix = f"sk-{app_prefix}"

        new_key = ApiKey(
            key_prefix=key_prefix,
            key_hash=key_hash,
            app_name=old_key.app_name,
            description=old_key.description,
            tier=old_key.tier,
            rate_limit=old_key.rate_limit,
            expires_at=old_key.expires_at,
        )

        db.add(new_key)
        await db.flush()

        logger.info(
            "API key regenerated",
            old_key_id=str(key_id),
            new_key_id=str(new_key.id),
            app_name=old_key.app_name,
        )

        return ApiKeyCreated(
            id=new_key.id,
            key=api_key,
            key_prefix=key_prefix,
            app_name=new_key.app_name,
            tier=new_key.tier,
            rate_limit=new_key.rate_limit,
            expires_at=new_key.expires_at,
            created_at=new_key.created_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to regenerate API key", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to regenerate API key: {str(e)}")


# =============================================================================
# Settings Endpoints
# =============================================================================

# In-memory settings (in production, store in database or config service)
_app_settings = AppSettings()


@router.get("/settings", response_model=AppSettings)
async def get_settings() -> AppSettings:
    """
    Get current application settings.
    """
    return _app_settings


@router.put("/settings", response_model=AppSettings)
async def update_settings(settings: AppSettings) -> AppSettings:
    """
    Update application settings.

    Note: Some settings may require service restart to take effect.
    """
    global _app_settings
    _app_settings = settings

    logger.info(
        "Settings updated",
        smtp_timeout=settings.smtp_timeout,
        smtp_concurrency=settings.smtp_concurrency,
    )

    return _app_settings


# =============================================================================
# Health & Info Endpoints
# =============================================================================

@router.get("/info")
async def get_admin_info() -> dict[str, Any]:
    """
    Get admin API information.

    Useful for Ops-Center to verify connectivity and capabilities.
    """
    return {
        "service": "Center Deep Data Intel",
        "admin_api_version": "1.0.0",
        "capabilities": [
            "dashboard_stats",
            "source_stats",
            "activity_tracking",
            "api_key_management",
            "settings_management",
        ],
        "endpoints": {
            "stats": "/api/v1/admin/stats",
            "stats_by_source": "/api/v1/admin/stats/by-source",
            "activity": "/api/v1/admin/activity",
            "activity_recent": "/api/v1/admin/activity/recent",
            "api_keys": "/api/v1/admin/api-keys",
            "settings": "/api/v1/admin/settings",
        },
        "timestamp": datetime.utcnow().isoformat(),
    }
