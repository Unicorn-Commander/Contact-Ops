"""
Catalogue service for Center Deep Data Intel.

Provides data normalization, deduplication, merging, and source tracking
functions for the data catalogue.
"""

import re
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.contact import Contact
from app.models.source import Source

logger = structlog.get_logger(__name__)


# ============================================================================
# Normalization Functions
# ============================================================================


def normalize_company(data: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize and standardize company data.

    Performs the following normalizations:
    - Cleans domain (removes protocol, trailing slashes)
    - Standardizes industry names
    - Normalizes location fields
    - Extracts enrichment data into separate field

    Args:
        data: Raw company data dictionary

    Returns:
        Normalized company data dictionary
    """
    normalized: dict[str, Any] = {}

    # Clean and normalize name
    if name := data.get("name"):
        normalized["name"] = _clean_string(name)

    # Clean and normalize domain
    if domain := data.get("domain"):
        normalized["primary_domain"] = _clean_domain(domain)
    elif website := data.get("website"):
        normalized["primary_domain"] = _clean_domain(website)

    # Normalize industry
    if industry := data.get("industry"):
        normalized["industry"] = _normalize_industry(industry)

    if sub_industry := data.get("sub_industry"):
        normalized["sub_industry"] = _clean_string(sub_industry)

    # Normalize employee count
    if employee_count := data.get("employee_count"):
        normalized["employee_count"] = _normalize_employee_count(employee_count)

    # Normalize location fields
    if city := data.get("city") or data.get("headquarters_city"):
        normalized["city"] = _clean_string(city)

    if state := data.get("state") or data.get("headquarters_state"):
        normalized["state"] = _normalize_state(state)

    if country := data.get("country") or data.get("headquarters_country"):
        normalized["country"] = _normalize_country(country)

    # Collect enrichment data (everything else)
    enrichment_fields = [
        "address", "postal_code", "website", "linkedin_url", "twitter_handle",
        "facebook_url", "description", "founded_year", "annual_revenue",
        "tags", "custom_fields", "phone", "fax", "naics_code", "sic_code",
        "revenue_range", "funding", "technologies", "keywords",
    ]

    enrichment_data: dict[str, Any] = {}
    for field in enrichment_fields:
        if value := data.get(field):
            enrichment_data[field] = value

    # Include any custom_fields directly
    if custom_fields := data.get("custom_fields"):
        if isinstance(custom_fields, dict):
            enrichment_data.update(custom_fields)

    if enrichment_data:
        normalized["enrichment_data"] = enrichment_data

    logger.debug("Normalized company data", original_keys=list(data.keys()), normalized_keys=list(normalized.keys()))

    return normalized


def normalize_contact(data: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize and standardize contact data.

    Performs the following normalizations:
    - Normalizes email to lowercase
    - Cleans name fields
    - Standardizes phone numbers
    - Extracts enrichment data

    Args:
        data: Raw contact data dictionary

    Returns:
        Normalized contact data dictionary
    """
    normalized: dict[str, Any] = {}

    # Normalize email
    if email := data.get("email"):
        normalized["email"] = email.lower().strip()

    # Handle name fields
    first_name = data.get("first_name")
    last_name = data.get("last_name")
    full_name = data.get("full_name") or data.get("name")

    if first_name:
        normalized["first_name"] = _clean_string(first_name)
    if last_name:
        normalized["last_name"] = _clean_string(last_name)

    # Construct full_name if not provided
    if full_name:
        normalized["full_name"] = _clean_string(full_name)
    elif first_name and last_name:
        normalized["full_name"] = f"{_clean_string(first_name)} {_clean_string(last_name)}"
    elif first_name:
        normalized["full_name"] = _clean_string(first_name)
    elif last_name:
        normalized["full_name"] = _clean_string(last_name)

    # Parse name from full_name if components not provided
    if not first_name and not last_name and full_name:
        parsed = _parse_name(full_name)
        if parsed.get("first_name"):
            normalized["first_name"] = parsed["first_name"]
        if parsed.get("last_name"):
            normalized["last_name"] = parsed["last_name"]

    # Normalize title
    if title := data.get("title"):
        normalized["title"] = _normalize_title(title)

    # Normalize phone numbers
    if phone := data.get("phone"):
        normalized["phone"] = _normalize_phone(phone)

    if mobile := data.get("mobile"):
        normalized["mobile"] = _normalize_phone(mobile)

    # Clean LinkedIn URL
    if linkedin_url := data.get("linkedin_url"):
        normalized["linkedin_url"] = _clean_linkedin_url(linkedin_url)

    # Collect enrichment data
    enrichment_fields = [
        "department", "seniority", "twitter_handle", "country", "city",
        "tags", "custom_fields", "role", "bio", "education", "skills",
        "languages", "certifications", "social_profiles",
    ]

    enrichment_data: dict[str, Any] = {}
    for field in enrichment_fields:
        if value := data.get(field):
            enrichment_data[field] = value

    # Include custom_fields directly
    if custom_fields := data.get("custom_fields"):
        if isinstance(custom_fields, dict):
            enrichment_data.update(custom_fields)

    if enrichment_data:
        normalized["enrichment_data"] = enrichment_data

    logger.debug("Normalized contact data", email=normalized.get("email"), has_name=bool(normalized.get("full_name")))

    return normalized


# ============================================================================
# Deduplication Functions
# ============================================================================


async def deduplicate_by_email(db: AsyncSession, email: str) -> Contact | None:
    """
    Find existing contact by email address.

    Args:
        db: Database session
        email: Email address to search for

    Returns:
        Existing Contact if found, None otherwise
    """
    email = email.lower().strip()

    result = await db.execute(
        select(Contact).where(Contact.primary_email == email)
    )
    contact = result.scalar_one_or_none()

    if contact:
        logger.debug("Found existing contact by email", email=email, contact_id=str(contact.id))

    return contact


async def deduplicate_by_domain(db: AsyncSession, domain: str) -> Company | None:
    """
    Find existing company by primary domain.

    Args:
        db: Database session
        domain: Domain to search for

    Returns:
        Existing Company if found, None otherwise
    """
    domain = _clean_domain(domain)

    result = await db.execute(
        select(Company).where(Company.primary_domain == domain)
    )
    company = result.scalar_one_or_none()

    if company:
        logger.debug("Found existing company by domain", domain=domain, company_id=str(company.id))

    return company


# ============================================================================
# Merge Functions
# ============================================================================


def merge_enrichment_data(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """
    Intelligently merge enrichment data from multiple sources.

    Merge strategy:
    - For scalar values: prefer new non-empty values
    - For lists: combine unique values
    - For dicts: recursively merge
    - Track data freshness via timestamps

    Args:
        existing: Existing enrichment data JSONB
        new: New enrichment data to merge

    Returns:
        Merged enrichment data dictionary
    """
    if not existing:
        return new.copy() if new else {}

    if not new:
        return existing.copy()

    merged = existing.copy()

    for key, new_value in new.items():
        existing_value = merged.get(key)

        if existing_value is None:
            # No existing value, use new
            merged[key] = new_value

        elif isinstance(new_value, dict) and isinstance(existing_value, dict):
            # Recursively merge dicts
            merged[key] = merge_enrichment_data(existing_value, new_value)

        elif isinstance(new_value, list) and isinstance(existing_value, list):
            # Combine lists, keeping unique values
            combined = existing_value.copy()
            for item in new_value:
                if item not in combined:
                    combined.append(item)
            merged[key] = combined

        elif new_value is not None and new_value != "":
            # Prefer new non-empty scalar value
            merged[key] = new_value

    # Track merge timestamp
    if "_merged_at" not in merged:
        merged["_merged_at"] = []

    if isinstance(merged["_merged_at"], list):
        merged["_merged_at"].append(datetime.utcnow().isoformat())
        # Keep only last 10 merge timestamps
        merged["_merged_at"] = merged["_merged_at"][-10:]

    logger.debug(
        "Merged enrichment data",
        existing_keys=len(existing),
        new_keys=len(new),
        merged_keys=len(merged),
    )

    return merged


# ============================================================================
# Source Tracking
# ============================================================================


async def track_source(
    db: AsyncSession,
    entity_type: str,
    entity_id: UUID,
    source_app: str,
    data: dict[str, Any],
    source_record_id: str | None = None,
    source_url: str | None = None,
) -> Source:
    """
    Record the source of data contribution.

    Creates a source tracking record linking an entity to the application
    that contributed data about it.

    Args:
        db: Database session
        entity_type: Type of entity ("company", "contact", "email")
        entity_id: ID of the entity
        source_app: Source application identifier
        data: Data contributed by this source
        source_record_id: Optional ID of the record in the source system
        source_url: Optional URL to the source record

    Returns:
        Created Source record
    """
    # Extract key fields to track what was contributed
    contributed_data = _extract_contributed_fields(data)

    source = Source(
        entity_type=entity_type,
        entity_id=entity_id,
        source_app=source_app,
        source_record_id=source_record_id,
        source_url=source_url,
        contributed_data=contributed_data,
    )

    db.add(source)

    logger.debug(
        "Tracked data source",
        entity_type=entity_type,
        entity_id=str(entity_id),
        source_app=source_app,
        fields_contributed=list(contributed_data.keys()) if contributed_data else [],
    )

    return source


def _extract_contributed_fields(data: dict[str, Any]) -> dict[str, Any]:
    """
    Extract and summarize which fields were contributed.

    For tracking purposes, we don't store full values but indicate
    which fields had data.

    Args:
        data: Full data dictionary

    Returns:
        Summary of contributed fields
    """
    contributed: dict[str, Any] = {}

    for key, value in data.items():
        if value is None or value == "" or value == []:
            continue

        if key == "enrichment_data" and isinstance(value, dict):
            # Track enrichment field names
            contributed["enrichment_fields"] = list(value.keys())
        elif isinstance(value, (str, int, float, bool)):
            # Indicate field was provided
            contributed[key] = True
        elif isinstance(value, list):
            contributed[key] = f"list[{len(value)}]"
        elif isinstance(value, dict):
            contributed[key] = f"dict[{len(value)}]"

    return contributed


# ============================================================================
# Helper Functions
# ============================================================================


def _clean_string(value: str) -> str:
    """Clean and normalize a string value."""
    if not value:
        return ""
    # Remove extra whitespace
    value = " ".join(value.split())
    return value.strip()


def _clean_domain(domain: str) -> str:
    """Clean and normalize a domain name."""
    if not domain:
        return ""

    domain = domain.lower().strip()

    # Remove protocol
    if domain.startswith("http://"):
        domain = domain[7:]
    elif domain.startswith("https://"):
        domain = domain[8:]

    # Remove www prefix
    if domain.startswith("www."):
        domain = domain[4:]

    # Remove path and query string
    domain = domain.split("/")[0].split("?")[0]

    return domain


def _normalize_industry(industry: str) -> str:
    """Normalize industry classification."""
    if not industry:
        return ""

    industry = _clean_string(industry)

    # Common industry mappings
    industry_map = {
        "tech": "Technology",
        "it": "Information Technology",
        "software": "Software",
        "saas": "Software",
        "healthcare": "Healthcare",
        "health care": "Healthcare",
        "finance": "Financial Services",
        "financial": "Financial Services",
        "banking": "Financial Services",
        "real estate": "Real Estate",
        "realestate": "Real Estate",
        "property": "Real Estate",
        "retail": "Retail",
        "ecommerce": "E-Commerce",
        "e-commerce": "E-Commerce",
        "manufacturing": "Manufacturing",
        "education": "Education",
        "edtech": "Education Technology",
        "construction": "Construction",
        "hospitality": "Hospitality",
        "legal": "Legal Services",
        "law": "Legal Services",
        "consulting": "Consulting",
        "marketing": "Marketing",
        "media": "Media & Entertainment",
        "entertainment": "Media & Entertainment",
        "telecom": "Telecommunications",
        "telecommunications": "Telecommunications",
        "automotive": "Automotive",
        "energy": "Energy",
        "oil and gas": "Energy",
        "utilities": "Utilities",
        "insurance": "Insurance",
        "pharma": "Pharmaceuticals",
        "pharmaceutical": "Pharmaceuticals",
        "biotech": "Biotechnology",
        "aerospace": "Aerospace & Defense",
        "defense": "Aerospace & Defense",
        "government": "Government",
        "nonprofit": "Non-Profit",
        "non-profit": "Non-Profit",
    }

    industry_lower = industry.lower()
    for key, value in industry_map.items():
        if key in industry_lower:
            return value

    # Title case if no mapping found
    return industry.title()


def _normalize_employee_count(count: Any) -> str:
    """Normalize employee count to a standard range format."""
    if not count:
        return ""

    if isinstance(count, int):
        if count <= 10:
            return "1-10"
        elif count <= 50:
            return "11-50"
        elif count <= 200:
            return "51-200"
        elif count <= 500:
            return "201-500"
        elif count <= 1000:
            return "501-1000"
        elif count <= 5000:
            return "1001-5000"
        elif count <= 10000:
            return "5001-10000"
        else:
            return "10000+"

    # Parse string formats
    count_str = str(count).lower().replace(",", "").replace(" ", "")

    # Handle range formats like "51-200" or "51 to 200"
    if re.match(r"^\d+-\d+$", count_str):
        return count_str

    # Handle "200+" or "200 employees"
    match = re.search(r"(\d+)", count_str)
    if match:
        num = int(match.group(1))
        return _normalize_employee_count(num)

    return str(count)


def _normalize_state(state: str) -> str:
    """Normalize US state names and abbreviations."""
    if not state:
        return ""

    state = _clean_string(state).upper()

    # US state abbreviation to full name
    state_map = {
        "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
        "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
        "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
        "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
        "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
        "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
        "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
        "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
        "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
        "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
        "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
        "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
        "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    }

    # Check for abbreviation
    if state in state_map:
        return state_map[state]

    # Return as-is (might be full name or non-US)
    return state.title()


def _normalize_country(country: str) -> str:
    """Normalize country names and codes."""
    if not country:
        return ""

    country = _clean_string(country).upper()

    # Common country code mappings
    country_map = {
        "US": "United States",
        "USA": "United States",
        "U.S.": "United States",
        "U.S.A.": "United States",
        "UNITED STATES OF AMERICA": "United States",
        "UK": "United Kingdom",
        "GB": "United Kingdom",
        "GREAT BRITAIN": "United Kingdom",
        "CA": "Canada",
        "CAN": "Canada",
        "AU": "Australia",
        "AUS": "Australia",
        "DE": "Germany",
        "GER": "Germany",
        "DEUTSCHLAND": "Germany",
        "FR": "France",
        "FRA": "France",
        "JP": "Japan",
        "JPN": "Japan",
        "CN": "China",
        "CHN": "China",
        "IN": "India",
        "IND": "India",
        "BR": "Brazil",
        "BRA": "Brazil",
        "MX": "Mexico",
        "MEX": "Mexico",
    }

    if country in country_map:
        return country_map[country]

    # Return title case
    return country.title()


def _normalize_phone(phone: str) -> str:
    """Normalize phone number format."""
    if not phone:
        return ""

    # Remove all non-digit characters except + at start
    digits = re.sub(r"[^\d+]", "", phone)

    # Ensure + is only at start
    if "+" in digits[1:]:
        digits = digits[0] + digits[1:].replace("+", "")

    # Format US numbers
    if len(digits) == 10:
        return f"+1-{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits.startswith("1"):
        return f"+{digits[0]}-{digits[1:4]}-{digits[4:7]}-{digits[7:]}"

    return digits


def _clean_linkedin_url(url: str) -> str:
    """Clean and normalize LinkedIn URL."""
    if not url:
        return ""

    url = url.strip()

    # Ensure it's a LinkedIn URL
    if "linkedin.com" not in url.lower():
        return url

    # Ensure https protocol
    if not url.startswith("http"):
        url = "https://" + url

    # Remove trailing slashes and query params
    url = url.split("?")[0].rstrip("/")

    return url


def _parse_name(full_name: str) -> dict[str, str]:
    """
    Parse a full name into first and last name components.

    Simple heuristic: first word is first name, rest is last name.

    Args:
        full_name: Full name string

    Returns:
        Dictionary with first_name and last_name
    """
    if not full_name:
        return {}

    parts = full_name.strip().split()

    if len(parts) == 1:
        return {"first_name": parts[0]}
    elif len(parts) == 2:
        return {"first_name": parts[0], "last_name": parts[1]}
    else:
        # First word is first name, rest is last name
        return {"first_name": parts[0], "last_name": " ".join(parts[1:])}


def _normalize_title(title: str) -> str:
    """Normalize job title."""
    if not title:
        return ""

    title = _clean_string(title)

    # Common title standardizations
    title_map = {
        "ceo": "Chief Executive Officer",
        "cto": "Chief Technology Officer",
        "cfo": "Chief Financial Officer",
        "coo": "Chief Operating Officer",
        "cmo": "Chief Marketing Officer",
        "cio": "Chief Information Officer",
        "cso": "Chief Security Officer",
        "cpo": "Chief Product Officer",
        "vp": "Vice President",
        "svp": "Senior Vice President",
        "evp": "Executive Vice President",
        "avp": "Assistant Vice President",
        "dir": "Director",
        "sr": "Senior",
        "jr": "Junior",
        "mgr": "Manager",
    }

    # Apply mappings for common abbreviations
    words = title.split()
    normalized_words = []
    for word in words:
        lower_word = word.lower().rstrip(".,")
        if lower_word in title_map:
            normalized_words.append(title_map[lower_word])
        else:
            normalized_words.append(word)

    return " ".join(normalized_words)


# ============================================================================
# High-Level Submission Processing
# ============================================================================


async def process_company_submission(
    db: AsyncSession,
    company_data: dict[str, Any],
    source_app: str,
    source_url: str | None = None,
    source_record_id: str | None = None,
    owner_user_id: UUID | None = None,
) -> tuple[Company, bool]:
    """
    Process a company submission with deduplication and source tracking.

    Args:
        db: Database session
        company_data: Raw company data
        source_app: Source application identifier
        source_url: Citation URL
        source_record_id: Original record ID in source
        owner_user_id: User who submitted

    Returns:
        Tuple of (Company, was_created)
    """
    # Normalize the data
    normalized = normalize_company(company_data)
    domain = normalized.get("primary_domain")

    # Check for existing company by domain
    existing = None
    if domain:
        existing = await deduplicate_by_domain(db, domain)

    if existing:
        # Merge data into existing company
        if normalized.get("enrichment_data"):
            existing.enrichment_data = merge_enrichment_data(
                existing.enrichment_data or {},
                normalized.get("enrichment_data", {}),
            )

        # Update fields that may have new values
        for field in ["name", "industry", "sub_industry", "employee_count"]:
            if normalized.get(field):
                setattr(existing, field, normalized[field])

        # Add source app to list if not present
        if source_app not in (existing.source_apps or []):
            existing.source_apps = (existing.source_apps or []) + [source_app]

        # Track source contribution
        await track_source(
            db, "company", existing.id, source_app,
            normalized, source_record_id, source_url
        )

        logger.info("Updated existing company", company_id=str(existing.id), domain=domain)
        return existing, False

    # Create new company
    company = Company(
        name=normalized.get("name", "Unknown"),
        primary_domain=domain,
        industry=normalized.get("industry"),
        sub_industry=normalized.get("sub_industry"),
        employee_count=normalized.get("employee_count"),
        headquarters_city=normalized.get("city"),
        headquarters_state=normalized.get("state"),
        headquarters_country=normalized.get("country"),
        enrichment_data=normalized.get("enrichment_data", {}),
        source_apps=[source_app],
        owner_user_id=owner_user_id,
    )

    db.add(company)
    await db.flush()  # Get the ID

    # Track source
    await track_source(
        db, "company", company.id, source_app,
        normalized, source_record_id, source_url
    )

    logger.info("Created new company", company_id=str(company.id), domain=domain)
    return company, True


async def process_contact_submission(
    db: AsyncSession,
    contact_data: dict[str, Any],
    source_app: str,
    source_url: str | None = None,
    source_record_id: str | None = None,
    company_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    email_status: str | None = None,
    email_score: int | None = None,
) -> tuple[Contact, bool]:
    """
    Process a contact submission with deduplication and source tracking.

    Args:
        db: Database session
        contact_data: Raw contact data
        source_app: Source application identifier
        source_url: Citation URL
        source_record_id: Original record ID in source
        company_id: Associated company ID
        owner_user_id: User who submitted
        email_status: Verification status
        email_score: Verification score

    Returns:
        Tuple of (Contact, was_created)
    """
    # Normalize the data
    normalized = normalize_contact(contact_data)
    email = normalized.get("email")

    if not email:
        raise ValueError("Contact email is required")

    # Check for existing contact by email
    existing = await deduplicate_by_email(db, email)

    if existing:
        # Merge enrichment data
        if normalized.get("enrichment_data"):
            existing.enrichment_data = merge_enrichment_data(
                existing.enrichment_data or {},
                normalized.get("enrichment_data", {}),
            )

        # Update fields
        for field in ["full_name", "first_name", "last_name", "title", "phone", "mobile", "linkedin_url"]:
            if normalized.get(field):
                setattr(existing, field, normalized[field])

        # Link to company if not already linked
        if company_id and not existing.company_id:
            existing.company_id = company_id

        # Update verification info
        if email_status:
            from datetime import datetime
            existing.enrichment_data = existing.enrichment_data or {}
            existing.enrichment_data["_email_status"] = email_status
            existing.enrichment_data["_email_score"] = email_score
            existing.enrichment_data["_email_verified_at"] = datetime.utcnow().isoformat()

        # Add source app
        if source_app not in (existing.source_apps or []):
            existing.source_apps = (existing.source_apps or []) + [source_app]

        # Track source
        await track_source(
            db, "contact", existing.id, source_app,
            normalized, source_record_id, source_url
        )

        logger.info("Updated existing contact", contact_id=str(existing.id), email=email)
        return existing, False

    # Create new contact
    contact = Contact(
        primary_email=email,
        full_name=normalized.get("full_name"),
        first_name=normalized.get("first_name"),
        last_name=normalized.get("last_name"),
        title=normalized.get("title"),
        phone=normalized.get("phone"),
        mobile=normalized.get("mobile"),
        linkedin_url=normalized.get("linkedin_url"),
        company_id=company_id,
        enrichment_data=normalized.get("enrichment_data", {}),
        source_apps=[source_app],
        owner_user_id=owner_user_id,
    )

    # Add verification info to enrichment
    if email_status:
        contact.enrichment_data = contact.enrichment_data or {}
        contact.enrichment_data["_email_status"] = email_status
        contact.enrichment_data["_email_score"] = email_score
        contact.enrichment_data["_email_verified_at"] = datetime.utcnow().isoformat()

    db.add(contact)
    await db.flush()

    # Track source
    await track_source(
        db, "contact", contact.id, source_app,
        normalized, source_record_id, source_url
    )

    logger.info("Created new contact", contact_id=str(contact.id), email=email)
    return contact, True


async def lookup_contact_by_email(
    db: AsyncSession,
    email: str,
    include_company: bool = True,
    include_sources: bool = True,
) -> Contact | None:
    """
    Look up a contact by email with optional related data.

    Args:
        db: Database session
        email: Email to look up
        include_company: Whether to load company relationship
        include_sources: Whether to load source records

    Returns:
        Contact if found, None otherwise
    """
    email = email.lower().strip()

    result = await db.execute(
        select(Contact).where(Contact.primary_email == email)
    )
    contact = result.scalar_one_or_none()

    if contact and include_sources:
        # Load sources
        sources_result = await db.execute(
            select(Source).where(
                Source.entity_type == "contact",
                Source.entity_id == contact.id
            )
        )
        contact._loaded_sources = sources_result.scalars().all()

    return contact


async def lookup_company_by_domain(
    db: AsyncSession,
    domain: str,
    include_contacts: bool = False,
) -> Company | None:
    """
    Look up a company by domain.

    Args:
        db: Database session
        domain: Domain to look up
        include_contacts: Whether to load contacts

    Returns:
        Company if found, None otherwise
    """
    domain = _clean_domain(domain)

    result = await db.execute(
        select(Company).where(Company.primary_domain == domain)
    )
    return result.scalar_one_or_none()
