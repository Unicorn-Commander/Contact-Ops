"""
Pydantic schemas for catalogue/company data submission endpoints.

These schemas define the models for submitting company and contact
information to the data intel catalogue.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    field_validator,
)


class CompanySize(str, Enum):
    """Company size classification."""

    STARTUP = "startup"  # 1-10 employees
    SMALL = "small"  # 11-50 employees
    MEDIUM = "medium"  # 51-200 employees
    LARGE = "large"  # 201-1000 employees
    ENTERPRISE = "enterprise"  # 1000+ employees
    UNKNOWN = "unknown"


class ContactRole(str, Enum):
    """Contact role classification."""

    CEO = "ceo"
    CTO = "cto"
    CFO = "cfo"
    COO = "coo"
    VP = "vp"
    DIRECTOR = "director"
    MANAGER = "manager"
    EXECUTIVE = "executive"
    SALES = "sales"
    MARKETING = "marketing"
    ENGINEERING = "engineering"
    FOUNDER = "founder"
    OTHER = "other"


class DataSource(str, Enum):
    """Source of the data."""

    MANUAL = "manual"
    IMPORT = "import"
    SCRAPE = "scrape"
    API = "api"
    ENRICHMENT = "enrichment"


class CompanyCreate(BaseModel):
    """Schema for creating a new company record."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "name": "Acme Corporation",
                "domain": "acme.com",
                "industry": "Technology",
                "size": "medium",
                "country": "US",
                "city": "San Francisco",
                "description": "Leading provider of innovative solutions",
                "linkedin_url": "https://linkedin.com/company/acme",
                "website": "https://acme.com",
            }
        },
    )

    # Required fields
    name: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Company name",
    )
    domain: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="Primary company domain (without protocol)",
    )

    # Optional classification
    industry: str | None = Field(
        default=None,
        max_length=255,
        description="Industry classification",
    )
    size: CompanySize = Field(
        default=CompanySize.UNKNOWN,
        description="Company size classification",
    )
    employee_count: int | None = Field(
        default=None,
        ge=0,
        description="Estimated number of employees",
    )

    # Location
    country: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 country code",
    )
    state: str | None = Field(
        default=None,
        max_length=100,
        description="State or province",
    )
    city: str | None = Field(
        default=None,
        max_length=255,
        description="City name",
    )
    address: str | None = Field(
        default=None,
        max_length=500,
        description="Full street address",
    )
    postal_code: str | None = Field(
        default=None,
        max_length=20,
        description="Postal/ZIP code",
    )

    # Online presence
    website: HttpUrl | None = Field(default=None, description="Company website URL")
    linkedin_url: HttpUrl | None = Field(default=None, description="LinkedIn company page URL")
    twitter_handle: str | None = Field(
        default=None,
        max_length=50,
        description="Twitter/X handle (without @)",
    )
    facebook_url: HttpUrl | None = Field(default=None, description="Facebook page URL")

    # Additional info
    description: str | None = Field(
        default=None,
        max_length=5000,
        description="Company description",
    )
    founded_year: int | None = Field(
        default=None,
        ge=1800,
        le=2100,
        description="Year the company was founded",
    )
    annual_revenue: float | None = Field(
        default=None,
        ge=0,
        description="Estimated annual revenue in USD",
    )

    # Metadata
    tags: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Custom tags for categorization",
    )
    custom_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional custom fields",
    )

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, v: str) -> str:
        """Remove protocol and trailing slashes from domain."""
        v = v.lower().strip()
        if v.startswith("http://"):
            v = v[7:]
        elif v.startswith("https://"):
            v = v[8:]
        return v.rstrip("/").split("/")[0]

    @field_validator("twitter_handle")
    @classmethod
    def clean_twitter_handle(cls, v: str | None) -> str | None:
        """Remove @ prefix from Twitter handle."""
        if v:
            return v.lstrip("@").lower()
        return v


class ContactCreate(BaseModel):
    """Schema for creating a new contact record."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "email": "REMOVED",
                "first_name": "John",
                "last_name": "Doe",
                "title": "Chief Technology Officer",
                "role": "cto",
                "phone": "REMOVED",
                "linkedin_url": "https://linkedin.com/in/johndoe",
            }
        },
    )

    # Required fields
    email: EmailStr = Field(..., description="Primary email address")

    # Name
    first_name: str | None = Field(
        default=None,
        max_length=100,
        description="First name",
    )
    last_name: str | None = Field(
        default=None,
        max_length=100,
        description="Last name",
    )
    full_name: str | None = Field(
        default=None,
        max_length=255,
        description="Full name (if first/last not available)",
    )

    # Professional info
    title: str | None = Field(
        default=None,
        max_length=255,
        description="Job title",
    )
    role: ContactRole = Field(
        default=ContactRole.OTHER,
        description="Role classification",
    )
    department: str | None = Field(
        default=None,
        max_length=100,
        description="Department name",
    )
    seniority: str | None = Field(
        default=None,
        max_length=50,
        description="Seniority level",
    )

    # Contact info
    phone: str | None = Field(
        default=None,
        max_length=50,
        description="Phone number",
    )
    mobile: str | None = Field(
        default=None,
        max_length=50,
        description="Mobile phone number",
    )

    # Online presence
    linkedin_url: HttpUrl | None = Field(default=None, description="LinkedIn profile URL")
    twitter_handle: str | None = Field(
        default=None,
        max_length=50,
        description="Twitter/X handle",
    )

    # Location (override company location)
    country: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 country code",
    )
    city: str | None = Field(
        default=None,
        max_length=255,
        description="City name",
    )

    # Metadata
    tags: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Custom tags",
    )
    custom_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional custom fields",
    )

    @field_validator("twitter_handle")
    @classmethod
    def clean_twitter_handle(cls, v: str | None) -> str | None:
        """Remove @ prefix from Twitter handle."""
        if v:
            return v.lstrip("@").lower()
        return v


class SourceInfo(BaseModel):
    """Information about the data source/citation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    app: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Source application identifier (e.g., 'loopnet-leads')",
    )
    url: HttpUrl | None = Field(
        default=None,
        description="Citation URL where this data was found",
    )
    record_id: str | None = Field(
        default=None,
        max_length=255,
        description="Original record ID in the source system",
    )
    captured_at: datetime | None = Field(
        default=None,
        description="When the data was captured from the source",
    )


class ConnectionCreate(BaseModel):
    """Schema for creating a relationship between entities."""

    model_config = ConfigDict(str_strip_whitespace=True)

    relationship_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Type of relationship (e.g., 'works_at', 'owns', 'manages')",
    )
    from_contact_email: EmailStr | None = Field(
        default=None,
        description="Email of the source contact (for contact->company)",
    )
    to_company_domain: str | None = Field(
        default=None,
        description="Domain of the target company",
    )
    to_contact_email: EmailStr | None = Field(
        default=None,
        description="Email of the target contact (for contact->contact)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional relationship metadata",
    )


class CatalogueSubmitRequest(BaseModel):
    """
    Request schema for submitting company and contact data to the catalogue.

    Supports submitting either:
    - Company only
    - Contact only (will attempt to match to existing company)
    - Company with contacts

    All data is deduplicated, stored, and verified.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "company": {
                    "name": "Sunrise Senior Living",
                    "domain": "sunrisesenior.com",
                    "industry": "Healthcare",
                    "city": "Sacramento",
                    "state": "CA",
                },
                "contacts": [
                    {
                        "email": "REMOVED",
                        "first_name": "John",
                        "last_name": "Smith",
                        "title": "Administrator",
                        "custom_fields": {
                            "license_number": "RCFE-123456"
                        }
                    }
                ],
                "source": {
                    "app": "ca-retirement-leads",
                    "url": "https://cdss.ca.gov/facility/123456",
                    "captured_at": "2026-01-17T10:30:00Z"
                },
                "connections": [
                    {
                        "relationship_type": "works_at",
                        "from_contact_email": "REMOVED",
                        "to_company_domain": "sunrisesenior.com"
                    }
                ]
            }
        },
    )

    # Data to submit
    company: CompanyCreate | None = Field(
        default=None,
        description="Company data to create/update",
    )
    contacts: list[ContactCreate] = Field(
        default_factory=list,
        max_length=1000,
        description="Contact records to create/update (max 1000)",
    )

    # Source/citation information (required)
    source: SourceInfo = Field(
        ...,
        description="Source information including app name and citation URL",
    )

    # Connections/relationships
    connections: list[ConnectionCreate] = Field(
        default_factory=list,
        max_length=100,
        description="Relationships between entities",
    )

    # Processing options
    deduplicate: bool = Field(
        default=True,
        description="Whether to merge with existing records (recommended)",
    )
    verify_emails: bool = Field(
        default=True,
        description="Whether to verify contact emails (required for storage)",
    )

    @field_validator("contacts", "company")
    @classmethod
    def check_not_empty(cls, v, info):
        """Ensure at least company or contacts is provided."""
        return v


class ContactVerificationResult(BaseModel):
    """Verification result for a single contact."""

    email: str
    status: str = Field(description="valid, invalid, disposable, syntax_error, error")
    is_deliverable: bool | None = None
    risk_level: str = Field(default="unknown", description="low, medium, high, unknown")
    score: int = Field(default=0, ge=0, le=100)
    catalogued: bool = Field(default=False, description="Whether the contact was stored")
    contact_id: str | None = Field(default=None, description="ID if catalogued")
    error_message: str | None = None


class CatalogueSubmitResponse(BaseModel):
    """Response schema for catalogue submission."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "company_id": "550e8400-e29b-41d4-a716-446655440000",
                "company_created": True,
                "contacts": [
                    {
                        "email": "REMOVED",
                        "status": "valid",
                        "is_deliverable": True,
                        "risk_level": "low",
                        "score": 95,
                        "catalogued": True,
                        "contact_id": "660e8400-e29b-41d4-a716-446655440001"
                    }
                ],
                "connections_created": 1,
            }
        },
    )

    success: bool = Field(..., description="Whether the submission succeeded")
    message: str | None = Field(default=None, description="Status message")

    # Company result
    company_id: str | None = Field(default=None, description="Created/updated company ID")
    company_created: bool = Field(default=False, description="Whether a new company was created")

    # Contact results with verification
    contacts: list[ContactVerificationResult] = Field(
        default_factory=list,
        description="Verification and storage results for each contact",
    )

    # Connection results
    connections_created: int = Field(default=0, ge=0, description="Number of connections created")

    # Errors
    errors: list[str] = Field(
        default_factory=list,
        description="List of errors encountered",
    )

    # Metadata
    processed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the request was processed",
    )


class CompanyLookupResponse(BaseModel):
    """Response for company lookup."""

    id: str
    name: str
    domain: str | None
    industry: str | None
    city: str | None
    state: str | None
    country: str | None
    employee_count: str | None
    website: str | None
    enrichment_data: dict[str, Any] = Field(default_factory=dict)
    source_apps: list[str] = Field(default_factory=list)
    contact_count: int = 0
    first_seen_at: datetime
    last_updated_at: datetime


class ContactLookupResponse(BaseModel):
    """Response for contact lookup."""

    id: str
    email: str
    full_name: str | None
    title: str | None
    phone: str | None
    linkedin_url: str | None
    company: CompanyLookupResponse | None = None
    email_status: str | None
    email_verified_at: datetime | None
    email_score: int | None
    enrichment_data: dict[str, Any] = Field(default_factory=dict)
    source_apps: list[str] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Citation URLs and source information",
    )
    first_seen_at: datetime
    last_updated_at: datetime


class CatalogueSearchRequest(BaseModel):
    """Request for searching the catalogue."""

    query: str | None = Field(default=None, description="Full-text search query")
    domain: str | None = Field(default=None, description="Filter by company domain")
    industry: str | None = Field(default=None, description="Filter by industry")
    state: str | None = Field(default=None, description="Filter by state")
    city: str | None = Field(default=None, description="Filter by city")
    title_contains: str | None = Field(default=None, description="Filter contacts by title")
    source_app: str | None = Field(default=None, description="Filter by source app")
    email_status: str | None = Field(default=None, description="Filter by email status (valid, invalid)")
    limit: int = Field(default=50, ge=1, le=500, description="Maximum results")
    offset: int = Field(default=0, ge=0, description="Pagination offset")


class CatalogueSearchResponse(BaseModel):
    """Response for catalogue search."""

    total: int = Field(description="Total matching records")
    limit: int
    offset: int
    results: list[ContactLookupResponse] = Field(default_factory=list)
