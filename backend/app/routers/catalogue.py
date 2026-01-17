"""
Catalogue endpoints for Center Deep Data Intel API.

Provides endpoints for:
- Submitting enriched company and contact data
- Bulk data submission and CSV uploads
- Looking up catalogued data by email or domain
- Searching across the data catalogue
- Catalogue statistics
"""

import csv
import io
import re
import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import AppContext, get_current_app
from app.core.rbac import Permission, check_permission
from app.core.lago_client import get_lago_client
from app.core.usage_tracker import check_usage_limit, increment_usage
from app.models.company import Company
from app.models.connection import Connection
from app.models.contact import Contact
from app.models.email import Email
from app.models.job import Job
from app.models.source import Source
from app.schemas.catalogue import (
    CatalogueSubmitRequest,
    CatalogueSubmitResponse,
    CompanyCreate,
    ContactCreate,
)
from app.services.catalogue_service import (
    deduplicate_by_domain,
    deduplicate_by_email,
    merge_enrichment_data,
    normalize_company,
    normalize_contact,
    track_source,
)
from app.services.phone_verifier import get_phone_verifier

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/catalogue", tags=["Catalogue"])


# ============================================================================
# Request/Response Schemas
# ============================================================================


class ContactSubmission(BaseModel):
    """Contact data for catalogue submission.

    At least one of email or phone is required for storage.
    Contacts are stored if they have a valid email OR a valid phone.
    """

    email: EmailStr | None = None  # Optional - stored if valid
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    title: str | None = None
    phone: str | None = None  # Optional - stored if valid
    mobile: str | None = None
    linkedin_url: str | None = None
    department: str | None = None
    seniority: str | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class CompanySubmission(BaseModel):
    """Company data for catalogue submission."""

    name: str
    domain: str | None = None
    industry: str | None = None
    sub_industry: str | None = None
    employee_count: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    address: str | None = None
    postal_code: str | None = None
    website: str | None = None
    linkedin_url: str | None = None
    description: str | None = None
    founded_year: int | None = None
    annual_revenue: float | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class SourceInfoInput(BaseModel):
    """Source/citation information for data submission."""

    app: str = Field(..., description="Source application identifier (e.g., 'loopnet-leads')")
    url: str | None = Field(default=None, description="Citation URL where this data was found")
    record_id: str | None = Field(default=None, description="Original record ID in the source system")


class ConnectionInput(BaseModel):
    """Relationship between entities."""

    relationship_type: str = Field(..., description="Type of relationship (e.g., 'works_at', 'owns')")
    from_contact_email: EmailStr | None = None
    to_company_domain: str | None = None


class CatalogueSubmitInput(BaseModel):
    """Request schema for POST /submit endpoint."""

    company: CompanySubmission | None = None
    contacts: list[ContactSubmission] = Field(default_factory=list)
    source: SourceInfoInput = Field(..., description="Source/citation information")
    connections: list[ConnectionInput] = Field(default_factory=list, description="Relationships between entities")
    verify_emails: bool = Field(default=True, description="Verify contact emails")
    verify_phones: bool = Field(default=True, description="Verify contact phone numbers")
    deduplicate: bool = Field(default=True, description="Merge with existing records")


class ContactVerificationResult(BaseModel):
    """Verification result for a contact."""

    id: str
    email: str | None = None
    phone: str | None = None
    email_verification: dict[str, Any] | None = None
    phone_verification: dict[str, Any] | None = None
    stored: bool = Field(default=False, description="Whether contact was stored in catalogue")


class SubmitResponse(BaseModel):
    """Response schema for POST /submit endpoint."""

    catalogued: bool
    company_id: str | None = None
    contacts: list[ContactVerificationResult] = Field(default_factory=list)
    message: str | None = None


class BulkRecord(BaseModel):
    """Single record in a bulk submission."""

    company: CompanySubmission | None = None
    contacts: list[ContactSubmission] = Field(default_factory=list)


class BulkSubmitRequest(BaseModel):
    """Request schema for POST /bulk endpoint."""

    records: list[BulkRecord] = Field(..., min_length=1, max_length=10000)
    source_app: str
    async_mode: bool = Field(default=False, alias="async")


class BulkSubmitResponse(BaseModel):
    """Response schema for POST /bulk endpoint."""

    job_id: str | None = None
    results: list[SubmitResponse] | None = None
    queued_count: int | None = None


class CSVUploadResponse(BaseModel):
    """Response schema for POST /upload/csv endpoint."""

    job_id: str
    detected_columns: list[str]
    row_count: int
    message: str | None = None


class EmailLookupResponse(BaseModel):
    """Response schema for GET /lookup/email/{email} endpoint."""

    found: bool
    contact: dict[str, Any] | None = None
    company: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None


class DomainLookupResponse(BaseModel):
    """Response schema for GET /lookup/domain/{domain} endpoint."""

    found: bool
    company: dict[str, Any] | None = None
    contacts: list[dict[str, Any]] = Field(default_factory=list)


class SearchResultItem(BaseModel):
    """Single search result item."""

    type: str  # "company" or "contact"
    id: str
    name: str | None = None
    email: str | None = None
    domain: str | None = None
    industry: str | None = None
    title: str | None = None
    company_name: str | None = None
    state: str | None = None
    confidence_score: float | None = None


class SearchResponse(BaseModel):
    """Response schema for GET /search endpoint."""

    total: int
    results: list[SearchResultItem]
    limit: int
    offset: int


class CatalogueStatsResponse(BaseModel):
    """Response schema for GET /stats endpoint."""

    companies: int
    contacts: int
    emails: int
    verified: int
    source_breakdown: dict[str, int]
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Endpoints
# ============================================================================


@router.post(
    "/submit",
    summary="Submit data for verification and cataloguing",
    description="Submit company and contact data. Emails are verified and valid data is stored with source citations.",
    response_model=SubmitResponse,
)
async def submit_data(
    request: CatalogueSubmitInput,
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SubmitResponse:
    """
    Submit enriched company and contact data to the catalogue.

    This endpoint:
    1. Verifies all contact emails via SMTP
    2. Stores only contacts with valid, deliverable emails
    3. Tracks source/citation URLs for all data
    4. Deduplicates against existing records
    5. Creates relationship connections between entities

    Args:
        request: Company and contact data to catalogue
        app: Authenticated application context
        db: Database session
        settings: Application settings

    Returns:
        SubmitResponse with catalogued status and contact verification results
    """
    # Check permission
    check_permission(app.permissions, Permission.CATALOGUE_SUBMIT)

    source_app = request.source.app
    source_url = request.source.url
    source_record_id = request.source.record_id
    email_count = len(request.contacts)

    # Check usage limit for verifications
    if app.user_id and email_count > 0:
        await check_usage_limit(db, app.user_id, "verify:batch", app.tier, count=email_count)

    logger.info(
        "Catalogue submission received",
        source_app=source_app,
        source_url=source_url,
        has_company=request.company is not None,
        contact_count=email_count,
        connection_count=len(request.connections),
        app_name=app.app_name,
        user_id=app.user_id,
    )

    company_id: str | None = None
    contact_results: list[ContactVerificationResult] = []
    connections_created = 0
    lago = get_lago_client()
    batch_id = str(uuid.uuid4())

    try:
        # Process company if provided
        if request.company:
            company_data = normalize_company(request.company.model_dump())
            domain = company_data.get("primary_domain")

            # Check for existing company by domain
            existing_company = None
            if domain and request.deduplicate:
                existing_company = await deduplicate_by_domain(db, domain)

            if existing_company:
                # Merge with existing company
                existing_company.enrichment_data = merge_enrichment_data(
                    existing_company.enrichment_data or {},
                    company_data.get("enrichment_data", {}),
                )
                # Update fields if new data is better
                for field in ["name", "industry", "sub_industry", "employee_count",
                              "headquarters_city", "headquarters_state", "headquarters_country"]:
                    new_value = company_data.get(field)
                    if new_value and not getattr(existing_company, field, None):
                        setattr(existing_company, field, new_value)

                # Track source
                if source_app not in (existing_company.source_apps or []):
                    existing_company.source_apps = (existing_company.source_apps or []) + [source_app]

                company_id = str(existing_company.id)
                await track_source(
                    db, "company", existing_company.id, source_app, company_data,
                    source_record_id=source_record_id, source_url=source_url
                )
            else:
                # Create new company
                new_company = Company(
                    name=company_data.get("name"),
                    primary_domain=domain,
                    industry=company_data.get("industry"),
                    sub_industry=company_data.get("sub_industry"),
                    employee_count=company_data.get("employee_count"),
                    headquarters_city=company_data.get("city"),
                    headquarters_state=company_data.get("state"),
                    headquarters_country=company_data.get("country"),
                    enrichment_data=company_data.get("enrichment_data", {}),
                    source_apps=[source_app],
                    owner_user_id=uuid.UUID(app.user_id) if app.user_id else None,
                )
                db.add(new_company)
                await db.flush()
                company_id = str(new_company.id)
                await track_source(
                    db, "company", new_company.id, source_app, company_data,
                    source_record_id=source_record_id, source_url=source_url
                )

        # Process contacts with verification (email OR phone required)
        phone_verifier = get_phone_verifier(veriphone_api_key=settings.VERIPHONE_API_KEY)

        for i, contact_data in enumerate(request.contacts):
            contact_dict = normalize_contact(contact_data.model_dump())
            email = contact_dict.get("email")
            phone = contact_dict.get("phone") or contact_dict.get("mobile")

            # Skip if no email and no phone
            if not email and not phone:
                continue

            # Verify email if provided
            email_verification = None
            email_verification_dict = None
            email_valid = False

            if email and request.verify_emails:
                from app.routers.verify import perform_verification
                email_verification = await perform_verification(email, settings)
                email_valid = (
                    email_verification.status.value == "valid"
                    and email_verification.is_deliverable
                    and email_verification.risk_level.value in ("low", "medium")
                )
                email_verification_dict = {
                    "status": email_verification.status.value,
                    "is_deliverable": email_verification.is_deliverable,
                    "is_disposable": email_verification.is_disposable,
                    "is_role_based": email_verification.is_role_based,
                    "risk_level": email_verification.risk_level.value,
                    "score": email_verification.score,
                }

            # Verify phone if provided
            phone_verification = None
            phone_verification_dict = None
            phone_valid = False

            if phone and request.verify_phones:
                phone_verification = await phone_verifier.verify(phone, use_api=False)
                phone_valid = (
                    phone_verification.is_valid
                    and phone_verification.score >= 50
                    and phone_verification.risk_level.value in ("low", "medium")
                )
                phone_verification_dict = {
                    "is_valid": phone_verification.is_valid,
                    "formatted_e164": phone_verification.formatted_e164,
                    "phone_type": phone_verification.phone_type.value,
                    "carrier_name": phone_verification.carrier_name,
                    "country_code": phone_verification.country_code,
                    "region": phone_verification.region,
                    "risk_level": phone_verification.risk_level.value,
                    "score": phone_verification.score,
                }

            # Store if valid email OR valid phone
            should_store = email_valid or phone_valid

            contact_id: str | None = None

            if should_store:
                # Check for existing contact by email (if valid)
                existing_contact = None
                if request.deduplicate and email and email_valid:
                    existing_contact = await deduplicate_by_email(db, email)

                if existing_contact:
                    # Merge with existing contact
                    existing_contact.enrichment_data = merge_enrichment_data(
                        existing_contact.enrichment_data or {},
                        contact_dict.get("enrichment_data", {}),
                    )

                    # Update email verification info
                    if email_verification:
                        existing_contact.enrichment_data["_email_status"] = email_verification.status.value
                        existing_contact.enrichment_data["_email_score"] = email_verification.score
                        existing_contact.enrichment_data["_email_verified_at"] = datetime.utcnow().isoformat()

                    # Update phone verification info
                    if phone_verification and phone_valid:
                        existing_contact.phone_verified = True
                        existing_contact.phone_e164 = phone_verification.formatted_e164
                        existing_contact.phone_type = phone_verification.phone_type.value
                        existing_contact.phone_carrier = phone_verification.carrier_name
                        existing_contact.phone_country_code = phone_verification.country_code
                        existing_contact.phone_region = phone_verification.region
                        existing_contact.phone_risk_level = phone_verification.risk_level.value
                        existing_contact.phone_score = phone_verification.score
                        existing_contact.phone_verified_at = datetime.utcnow()

                    # Update fields if new data is better
                    for field in ["full_name", "first_name", "last_name", "title",
                                  "phone", "mobile", "linkedin_url"]:
                        new_value = contact_dict.get(field)
                        if new_value and not getattr(existing_contact, field, None):
                            setattr(existing_contact, field, new_value)

                    # Link to company if not already linked
                    if company_id and not existing_contact.company_id:
                        existing_contact.company_id = uuid.UUID(company_id)

                    # Track source
                    if source_app not in (existing_contact.source_apps or []):
                        existing_contact.source_apps = (existing_contact.source_apps or []) + [source_app]

                    contact_id = str(existing_contact.id)
                    await track_source(
                        db, "contact", existing_contact.id, source_app, contact_dict,
                        source_record_id=source_record_id, source_url=source_url
                    )
                else:
                    # Create new contact
                    enrichment = contact_dict.get("enrichment_data", {})
                    if email_verification:
                        enrichment["_email_status"] = email_verification.status.value
                        enrichment["_email_score"] = email_verification.score
                        enrichment["_email_verified_at"] = datetime.utcnow().isoformat()

                    new_contact = Contact(
                        company_id=uuid.UUID(company_id) if company_id else None,
                        primary_email=email if email_valid else None,
                        full_name=contact_dict.get("full_name") or contact_dict.get("name"),
                        first_name=contact_dict.get("first_name"),
                        last_name=contact_dict.get("last_name"),
                        title=contact_dict.get("title"),
                        phone=contact_dict.get("phone"),
                        mobile=contact_dict.get("mobile"),
                        linkedin_url=contact_dict.get("linkedin_url"),
                        enrichment_data=enrichment,
                        source_apps=[source_app],
                        owner_user_id=uuid.UUID(app.user_id) if app.user_id else None,
                        # Phone verification fields
                        phone_verified=phone_valid if phone_verification else None,
                        phone_e164=phone_verification.formatted_e164 if phone_verification and phone_valid else None,
                        phone_type=phone_verification.phone_type.value if phone_verification and phone_valid else None,
                        phone_carrier=phone_verification.carrier_name if phone_verification and phone_valid else None,
                        phone_country_code=phone_verification.country_code if phone_verification and phone_valid else None,
                        phone_region=phone_verification.region if phone_verification and phone_valid else None,
                        phone_risk_level=phone_verification.risk_level.value if phone_verification and phone_valid else None,
                        phone_score=phone_verification.score if phone_verification and phone_valid else None,
                        phone_verified_at=datetime.utcnow() if phone_verification and phone_valid else None,
                    )
                    db.add(new_contact)
                    await db.flush()
                    contact_id = str(new_contact.id)
                    await track_source(
                        db, "contact", new_contact.id, source_app, contact_dict,
                        source_record_id=source_record_id, source_url=source_url
                    )

                # Store email verification record if email is valid
                if email and email_valid and contact_id:
                    await _verify_and_store_email(
                        db, email, uuid.UUID(contact_id), source_app, settings
                    )

            # Send billing event for verification (count both email and phone)
            if app.user_id:
                if email:
                    await lago.send_verification_event(
                        user_id=app.user_id,
                        verification_id=f"{batch_id}-{i}-email",
                        email=email,
                    )

            contact_results.append(
                ContactVerificationResult(
                    id=contact_id or "",
                    email=email,
                    phone=phone,
                    email_verification=email_verification_dict,
                    phone_verification=phone_verification_dict,
                    stored=should_store,
                )
            )

        # Process connections
        for conn in request.connections:
            try:
                from_contact = None
                to_company = None

                if conn.from_contact_email:
                    from_contact = await deduplicate_by_email(db, str(conn.from_contact_email))

                if conn.to_company_domain:
                    to_company = await deduplicate_by_domain(db, conn.to_company_domain)

                if from_contact and to_company:
                    connection = Connection(
                        from_entity_type="contact",
                        from_entity_id=from_contact.id,
                        to_entity_type="company",
                        to_entity_id=to_company.id,
                        relationship_type=conn.relationship_type,
                        source_app=source_app,
                        source_url=source_url,
                    )
                    db.add(connection)
                    connections_created += 1
            except Exception as conn_err:
                logger.warning("Failed to create connection", error=str(conn_err))

        await db.commit()

        # Increment usage
        if app.user_id and email_count > 0:
            await increment_usage(db, app.user_id, "verify:batch", count=email_count)

        catalogued_count = sum(1 for r in contact_results if r.id)
        logger.info(
            "Catalogue submission complete",
            source_app=source_app,
            company_id=company_id,
            contacts_verified=len(contact_results),
            contacts_catalogued=catalogued_count,
            connections_created=connections_created,
        )

        return SubmitResponse(
            catalogued=True,
            company_id=company_id,
            contacts=contact_results,
            message=f"Verified {len(contact_results)} emails, catalogued {catalogued_count} valid contacts",
        )

    except Exception as e:
        logger.error("Catalogue submission failed", error=str(e), exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to catalogue data: {str(e)}",
        )


@router.post(
    "/bulk",
    summary="Submit bulk data to catalogue",
    description="Submit multiple company/contact records. Use async=true for large batches.",
    response_model=BulkSubmitResponse,
)
async def submit_bulk(
    request: BulkSubmitRequest,
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BulkSubmitResponse:
    """
    Submit multiple records to the catalogue in bulk.

    For large batches (>100) or when async=true, creates a background job.
    Otherwise processes synchronously.

    Args:
        request: Bulk submission request with records
        app: Authenticated application context
        db: Database session
        settings: Application settings

    Returns:
        BulkSubmitResponse with job_id or immediate results
    """
    record_count = len(request.records)

    logger.info(
        "Bulk catalogue submission",
        record_count=record_count,
        async_mode=request.async_mode,
        source_app=request.source_app,
        app_name=app.app_name,
    )

    # Async mode - create job and return ID
    if request.async_mode or record_count > 100:
        job = Job(
            job_type="catalogue_bulk",
            status="queued",
            total_count=record_count,
            processed_count=0,
            input_data={
                "records": [r.model_dump() for r in request.records],
                "source_app": request.source_app,
            },
            created_by_app=app.app_name,
            created_by_user=app.user_id,
        )
        db.add(job)
        await db.commit()

        logger.info("Created bulk catalogue job", job_id=str(job.id), record_count=record_count)

        # TODO: Queue job to background worker (Celery/ARQ/etc.)

        return BulkSubmitResponse(
            job_id=str(job.id),
            queued_count=record_count,
        )

    # Synchronous mode - process immediately
    results: list[SubmitResponse] = []

    for record in request.records:
        try:
            submit_input = CatalogueSubmitInput(
                company=record.company,
                contacts=record.contacts,
                source_app=request.source_app,
                verify_emails=False,
                deduplicate=True,
            )
            result = await submit_data(submit_input, app, db, settings)
            results.append(result)
        except Exception as e:
            logger.warning("Failed to process bulk record", error=str(e))
            results.append(SubmitResponse(catalogued=False, message=str(e)))

    logger.info(
        "Bulk catalogue submission complete",
        total=record_count,
        successful=sum(1 for r in results if r.catalogued),
    )

    return BulkSubmitResponse(results=results)


@router.post(
    "/upload/csv",
    summary="Upload CSV file for cataloguing",
    description="Upload a CSV file with company/contact data. Returns job_id for tracking.",
    response_model=CSVUploadResponse,
)
async def upload_csv(
    file: UploadFile = File(..., description="CSV file to upload"),
    source_app: str = Query("csv_upload", description="Source application identifier"),
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
) -> CSVUploadResponse:
    """
    Upload a CSV file for processing and cataloguing.

    Detects columns automatically and creates a processing job.

    Args:
        file: Uploaded CSV file
        source_app: Source application identifier
        app: Authenticated application context
        db: Database session

    Returns:
        CSVUploadResponse with job_id and detected columns
    """
    logger.info(
        "CSV upload received",
        filename=file.filename,
        content_type=file.content_type,
        source_app=source_app,
        app_name=app.app_name,
    )

    # Validate file type
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a CSV file",
        )

    try:
        # Read file content
        content = await file.read()
        text_content = content.decode("utf-8-sig")  # Handle BOM

        # Parse CSV to detect columns
        reader = csv.DictReader(io.StringIO(text_content))
        detected_columns = reader.fieldnames or []

        # Count rows
        rows = list(reader)
        row_count = len(rows)

        if row_count == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CSV file is empty",
            )

        # Detect column types
        column_mapping = _detect_csv_columns(detected_columns)

        logger.info(
            "CSV parsed",
            columns=detected_columns,
            row_count=row_count,
            column_mapping=column_mapping,
        )

        # Create processing job
        job = Job(
            job_type="csv_upload",
            status="queued",
            total_count=row_count,
            processed_count=0,
            input_data={
                "filename": file.filename,
                "columns": detected_columns,
                "column_mapping": column_mapping,
                "rows": rows,
                "source_app": source_app,
            },
            created_by_app=app.app_name,
            created_by_user=app.user_id,
        )
        db.add(job)
        await db.commit()

        logger.info("Created CSV processing job", job_id=str(job.id), row_count=row_count)

        # TODO: Queue job to background worker

        return CSVUploadResponse(
            job_id=str(job.id),
            detected_columns=detected_columns,
            row_count=row_count,
            message=f"Job created. Detected {len(column_mapping)} mappable columns.",
        )

    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file encoding. Please use UTF-8 encoded CSV.",
        )
    except csv.Error as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid CSV format: {str(e)}",
        )


@router.get(
    "/lookup/email/{email}",
    summary="Lookup catalogued data by email",
    description="Returns catalogued contact, company, and verification data for an email.",
    response_model=EmailLookupResponse,
)
async def lookup_email(
    email: str,
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
) -> EmailLookupResponse:
    """
    Look up catalogued data for an email address.

    Args:
        email: Email address to look up
        app: Authenticated application context
        db: Database session

    Returns:
        EmailLookupResponse with contact, company, and verification data
    """
    # Check permission - need either lookup:own or lookup:cross
    has_own = Permission.CATALOGUE_LOOKUP_OWN.value in app.permissions
    has_cross = Permission.CATALOGUE_LOOKUP_CROSS.value in app.permissions

    if not has_own and not has_cross:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature requires catalogue:lookup:own or catalogue:lookup:cross permission",
        )

    email = email.lower().strip()

    logger.debug("Email lookup", email=email, app_name=app.app_name)

    # Build query with access control
    contact_query = select(Contact).where(Contact.primary_email == email)

    # Apply owner filter if user only has lookup:own permission
    if has_own and not has_cross:
        if app.user_id:
            contact_query = contact_query.where(
                Contact.owner_user_id == uuid.UUID(app.user_id)
            )

    # Look up contact by primary email
    contact_result = await db.execute(contact_query)
    contact = contact_result.scalar_one_or_none()

    # Look up email record
    email_result = await db.execute(
        select(Email).where(Email.email == email)
    )
    email_record = email_result.scalar_one_or_none()

    if not contact and not email_record:
        return EmailLookupResponse(found=False)

    response_data: dict[str, Any] = {"found": True}

    if contact:
        response_data["contact"] = {
            "id": str(contact.id),
            "email": contact.primary_email,
            "full_name": contact.full_name,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "title": contact.title,
            "phone": contact.phone,
            "linkedin_url": contact.linkedin_url,
            "enrichment_data": contact.enrichment_data,
            "source_apps": contact.source_apps,
            "first_seen_at": contact.first_seen_at.isoformat() if contact.first_seen_at else None,
        }

        # Get associated company
        if contact.company_id:
            company_result = await db.execute(
                select(Company).where(Company.id == contact.company_id)
            )
            company = company_result.scalar_one_or_none()
            if company:
                response_data["company"] = {
                    "id": str(company.id),
                    "name": company.name,
                    "domain": company.primary_domain,
                    "industry": company.industry,
                    "city": company.headquarters_city,
                    "state": company.headquarters_state,
                    "country": company.headquarters_country,
                    "employee_count": company.employee_count,
                    "enrichment_data": company.enrichment_data,
                }

    if email_record:
        response_data["verification"] = {
            "email": email_record.email,
            "domain": email_record.domain,
            "is_valid": email_record.is_valid,
            "is_verified_smtp": email_record.is_verified_smtp,
            "is_catchall": email_record.is_catchall,
            "is_disposable": email_record.is_disposable,
            "is_role_based": email_record.is_role_based,
            "risk_score": float(email_record.risk_score) if email_record.risk_score else None,
            "last_verified_at": email_record.last_verified_at.isoformat() if email_record.last_verified_at else None,
            "verification_count": email_record.verification_count,
        }

    return EmailLookupResponse(**response_data)


@router.get(
    "/lookup/domain/{domain}",
    summary="Lookup catalogued data by domain",
    description="Returns company and all contacts for a domain.",
    response_model=DomainLookupResponse,
)
async def lookup_domain(
    domain: str,
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
) -> DomainLookupResponse:
    """
    Look up catalogued data for a domain.

    Args:
        domain: Domain to look up
        app: Authenticated application context
        db: Database session

    Returns:
        DomainLookupResponse with company and contacts
    """
    # Check permission - need either lookup:own or lookup:cross
    has_own = Permission.CATALOGUE_LOOKUP_OWN.value in app.permissions
    has_cross = Permission.CATALOGUE_LOOKUP_CROSS.value in app.permissions

    if not has_own and not has_cross:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature requires catalogue:lookup:own or catalogue:lookup:cross permission",
        )

    # Clean domain
    domain = domain.lower().strip()
    if domain.startswith("http://"):
        domain = domain[7:]
    elif domain.startswith("https://"):
        domain = domain[8:]
    domain = domain.rstrip("/").split("/")[0]

    logger.debug("Domain lookup", domain=domain, app_name=app.app_name)

    # Build query with access control
    company_query = select(Company).where(Company.primary_domain == domain)

    # Apply owner filter if user only has lookup:own permission
    if has_own and not has_cross:
        if app.user_id:
            company_query = company_query.where(
                Company.owner_user_id == uuid.UUID(app.user_id)
            )

    # Look up company by domain
    company_result = await db.execute(company_query)
    company = company_result.scalar_one_or_none()

    if not company:
        # Try to find contacts with emails from this domain
        contacts_result = await db.execute(
            select(Contact).where(Contact.primary_email.like(f"%@{domain}"))
        )
        contacts = contacts_result.scalars().all()

        if not contacts:
            return DomainLookupResponse(found=False)

        return DomainLookupResponse(
            found=True,
            contacts=[
                {
                    "id": str(c.id),
                    "email": c.primary_email,
                    "full_name": c.full_name,
                    "title": c.title,
                    "source_apps": c.source_apps,
                }
                for c in contacts
            ],
        )

    # Get all contacts for this company
    contacts_result = await db.execute(
        select(Contact).where(Contact.company_id == company.id)
    )
    contacts = contacts_result.scalars().all()

    return DomainLookupResponse(
        found=True,
        company={
            "id": str(company.id),
            "name": company.name,
            "domain": company.primary_domain,
            "industry": company.industry,
            "sub_industry": company.sub_industry,
            "city": company.headquarters_city,
            "state": company.headquarters_state,
            "country": company.headquarters_country,
            "employee_count": company.employee_count,
            "enrichment_data": company.enrichment_data,
            "source_apps": company.source_apps,
            "first_seen_at": company.first_seen_at.isoformat() if company.first_seen_at else None,
        },
        contacts=[
            {
                "id": str(c.id),
                "email": c.primary_email,
                "full_name": c.full_name,
                "first_name": c.first_name,
                "last_name": c.last_name,
                "title": c.title,
                "phone": c.phone,
                "linkedin_url": c.linkedin_url,
                "source_apps": c.source_apps,
            }
            for c in contacts
        ],
    )


@router.get(
    "/search",
    summary="Search catalogue",
    description="Full-text search across companies and contacts.",
    response_model=SearchResponse,
)
async def search_catalogue(
    q: str = Query(None, description="Search query"),
    industry: str = Query(None, description="Filter by industry"),
    state: str = Query(None, description="Filter by state"),
    min_confidence: float = Query(None, ge=0, le=1, description="Minimum confidence score"),
    limit: int = Query(50, ge=1, le=500, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Result offset for pagination"),
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """
    Search across catalogued companies and contacts.

    Supports full-text search and filtering by industry, state, etc.

    Args:
        q: Search query string
        industry: Filter by industry
        state: Filter by state
        min_confidence: Minimum confidence score for contacts
        limit: Maximum results to return
        offset: Pagination offset
        app: Authenticated application context
        db: Database session

    Returns:
        SearchResponse with matching results
    """
    # Check permission - need either search:own or search:cross
    has_own = Permission.CATALOGUE_SEARCH_OWN.value in app.permissions
    has_cross = Permission.CATALOGUE_SEARCH_CROSS.value in app.permissions

    if not has_own and not has_cross:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature requires catalogue:search:own or catalogue:search:cross permission",
        )

    logger.debug(
        "Catalogue search",
        query=q,
        industry=industry,
        state=state,
        limit=limit,
        offset=offset,
        app_name=app.app_name,
        has_cross_permission=has_cross,
    )

    results: list[SearchResultItem] = []
    total = 0

    # Build company query
    company_query = select(Company)
    company_filters = []

    # Apply owner filter if user only has search:own permission
    if has_own and not has_cross:
        if app.user_id:
            company_filters.append(Company.owner_user_id == uuid.UUID(app.user_id))

    if q:
        search_pattern = f"%{q}%"
        company_filters.append(
            or_(
                Company.name.ilike(search_pattern),
                Company.primary_domain.ilike(search_pattern),
                Company.industry.ilike(search_pattern),
            )
        )

    if industry:
        company_filters.append(Company.industry.ilike(f"%{industry}%"))

    if state:
        company_filters.append(Company.headquarters_state.ilike(f"%{state}%"))

    if company_filters:
        company_query = company_query.where(*company_filters)

    # Count total companies
    count_query = select(func.count()).select_from(company_query.subquery())
    company_count = (await db.execute(count_query)).scalar() or 0
    total += company_count

    # Fetch companies
    company_query = company_query.limit(limit).offset(offset)
    company_result = await db.execute(company_query)
    companies = company_result.scalars().all()

    for c in companies:
        results.append(
            SearchResultItem(
                type="company",
                id=str(c.id),
                name=c.name,
                domain=c.primary_domain,
                industry=c.industry,
                state=c.headquarters_state,
            )
        )

    # Build contact query if we need more results
    remaining = limit - len(results)
    if remaining > 0:
        contact_query = select(Contact)
        contact_filters = []

        # Apply owner filter if user only has search:own permission
        if has_own and not has_cross:
            if app.user_id:
                contact_filters.append(Contact.owner_user_id == uuid.UUID(app.user_id))

        if q:
            search_pattern = f"%{q}%"
            contact_filters.append(
                or_(
                    Contact.full_name.ilike(search_pattern),
                    Contact.primary_email.ilike(search_pattern),
                    Contact.title.ilike(search_pattern),
                )
            )

        if min_confidence is not None:
            contact_filters.append(Contact.confidence_score >= min_confidence)

        if contact_filters:
            contact_query = contact_query.where(*contact_filters)

        # Count total contacts
        contact_count_query = select(func.count()).select_from(contact_query.subquery())
        contact_count = (await db.execute(contact_count_query)).scalar() or 0
        total += contact_count

        # Adjust offset for contacts
        contact_offset = max(0, offset - company_count)
        contact_query = contact_query.limit(remaining).offset(contact_offset)
        contact_result = await db.execute(contact_query)
        contacts = contact_result.scalars().all()

        for ct in contacts:
            results.append(
                SearchResultItem(
                    type="contact",
                    id=str(ct.id),
                    name=ct.full_name,
                    email=ct.primary_email,
                    title=ct.title,
                    confidence_score=float(ct.confidence_score) if ct.confidence_score else None,
                )
            )

    return SearchResponse(
        total=total,
        results=results,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/stats",
    summary="Get catalogue statistics",
    description="Returns overall catalogue statistics.",
    response_model=CatalogueStatsResponse,
)
async def get_stats(
    app: AppContext = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
) -> CatalogueStatsResponse:
    """
    Get catalogue statistics.

    Returns counts of companies, contacts, emails, and breakdown by source.

    Args:
        app: Authenticated application context
        db: Database session

    Returns:
        CatalogueStatsResponse with statistics
    """
    logger.debug("Catalogue stats request", app_name=app.app_name)

    # Count companies
    companies_count = (
        await db.execute(select(func.count()).select_from(Company))
    ).scalar() or 0

    # Count contacts
    contacts_count = (
        await db.execute(select(func.count()).select_from(Contact))
    ).scalar() or 0

    # Count emails
    emails_count = (
        await db.execute(select(func.count()).select_from(Email))
    ).scalar() or 0

    # Count verified emails
    verified_count = (
        await db.execute(
            select(func.count()).select_from(Email).where(Email.is_verified_smtp == True)
        )
    ).scalar() or 0

    # Source breakdown - count sources by source_app
    source_result = await db.execute(
        select(Source.source_app, func.count(Source.id))
        .group_by(Source.source_app)
    )
    source_breakdown = {row[0]: row[1] for row in source_result.all()}

    return CatalogueStatsResponse(
        companies=companies_count,
        contacts=contacts_count,
        emails=emails_count,
        verified=verified_count,
        source_breakdown=source_breakdown,
    )


# ============================================================================
# Helper Functions
# ============================================================================


def _detect_csv_columns(columns: list[str]) -> dict[str, str]:
    """
    Detect and map CSV columns to standard fields.

    Args:
        columns: List of column names from CSV header

    Returns:
        Mapping of CSV column names to standard field names
    """
    mapping: dict[str, str] = {}

    # Common column name patterns
    patterns = {
        "email": r"^(email|e[-_]?mail|email[-_]?address)$",
        "first_name": r"^(first[-_]?name|fname|given[-_]?name)$",
        "last_name": r"^(last[-_]?name|lname|surname|family[-_]?name)$",
        "full_name": r"^(full[-_]?name|name|contact[-_]?name)$",
        "title": r"^(title|job[-_]?title|position)$",
        "phone": r"^(phone|telephone|phone[-_]?number)$",
        "mobile": r"^(mobile|cell|cell[-_]?phone)$",
        "company_name": r"^(company|company[-_]?name|organization|org)$",
        "company_domain": r"^(domain|company[-_]?domain|website|url)$",
        "industry": r"^(industry|sector)$",
        "city": r"^(city|town)$",
        "state": r"^(state|province|region)$",
        "country": r"^(country|nation)$",
        "linkedin_url": r"^(linkedin|linkedin[-_]?url|linkedin[-_]?profile)$",
    }

    for col in columns:
        col_lower = col.lower().strip()
        for field, pattern in patterns.items():
            if re.match(pattern, col_lower, re.IGNORECASE):
                mapping[col] = field
                break

    return mapping


async def _verify_and_store_email(
    db: AsyncSession,
    email: str,
    contact_id: uuid.UUID,
    source_app: str,
    settings: Settings,
) -> dict[str, Any]:
    """
    Verify an email and store the result.

    Args:
        db: Database session
        email: Email address to verify
        contact_id: Associated contact ID
        source_app: Source application
        settings: Application settings

    Returns:
        Verification result dictionary
    """
    from app.routers.verify import perform_verification

    # Perform verification
    result = await perform_verification(email, settings)

    # Check for existing email record
    email_result = await db.execute(
        select(Email).where(Email.email == email.lower())
    )
    email_record = email_result.scalar_one_or_none()

    domain = email.split("@")[1].lower() if "@" in email else ""

    if email_record:
        # Update existing record
        email_record.is_valid = result.is_deliverable
        email_record.is_verified_smtp = result.status.value == "valid"
        email_record.is_catchall = result.is_catch_all
        email_record.is_disposable = result.is_disposable
        email_record.is_role_based = result.is_role_based
        email_record.risk_score = result.score / 100 if result.score else None
        email_record.last_verified_at = datetime.utcnow()
        email_record.verification_count += 1
        if not email_record.contact_id:
            email_record.contact_id = contact_id
    else:
        # Create new email record
        email_record = Email(
            email=email.lower(),
            domain=domain,
            contact_id=contact_id,
            is_valid=result.is_deliverable,
            is_verified_smtp=result.status.value == "valid",
            is_catchall=result.is_catch_all,
            is_disposable=result.is_disposable,
            is_role_based=result.is_role_based,
            risk_score=result.score / 100 if result.score else None,
            last_verified_at=datetime.utcnow(),
            verification_count=1,
            discovered_by_app=source_app,
        )
        db.add(email_record)

    await db.flush()

    return {
        "email": email,
        "status": result.status.value,
        "is_deliverable": result.is_deliverable,
        "is_disposable": result.is_disposable,
        "is_role_based": result.is_role_based,
        "risk_level": result.risk_level.value,
        "score": result.score,
        "verified_at": datetime.utcnow().isoformat(),
    }
