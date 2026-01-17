# Center Deep Verify - Integration Guide

## Overview

Center Deep Verify provides email verification and data cataloguing services. When your app submits data for verification, we:

1. **Verify all emails** via SMTP
2. **Store valid data** in our unified catalogue
3. **Track sources** with citation URLs
4. **Return results** with verification status

This creates a growing data asset while providing verification as a service.

## Authentication

Add your API key to the `Authorization` header:

```
REMOVED
```

## Primary Endpoint: Submit Data

**Endpoint:** `POST https://verify.centerdeep.online/api/v1/catalogue/submit`

This is the main integration point. Submit your enriched data here for verification and cataloguing.

### Request Format

```python
import httpx

async def submit_to_verify(
    api_key: str,
    company: dict,
    contacts: list[dict],
    source_app: str,
    source_url: str = None,
):
    """Submit company and contacts for verification and cataloguing."""

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://verify.centerdeep.online/api/v1/catalogue/submit",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "company": company,
                "contacts": contacts,
                "source": {
                    "app": source_app,
                    "url": source_url,  # Citation URL where data was found
                },
                "connections": [],  # Optional relationships
            },
            timeout=60.0,  # Allow time for SMTP verification
        )
        response.raise_for_status()
        return response.json()
```

### Example Request

```json
{
  "company": {
    "name": "Sunrise Senior Living",
    "domain": "sunrisesenior.com",
    "industry": "Healthcare",
    "city": "Sacramento",
    "state": "CA",
    "phone": "REMOVED",
    "custom_fields": {
      "license_number": "RCFE-123456",
      "bed_count": 45
    }
  },
  "contacts": [
    {
      "email": "REMOVED",
      "first_name": "John",
      "last_name": "Smith",
      "title": "Administrator",
      "phone": "REMOVED",
      "linkedin_url": "https://linkedin.com/in/johnsmith",
      "custom_fields": {
        "years_in_role": 5
      }
    },
    {
      "email": "REMOVED",
      "first_name": "Jane",
      "last_name": "Doe",
      "title": "Director of Operations"
    }
  ],
  "source": {
    "app": "ca-retirement-leads",
    "url": "https://cdss.ca.gov/facility/123456",
    "record_id": "FAC-123456"
  },
  "connections": [
    {
      "relationship_type": "works_at",
      "from_contact_email": "REMOVED",
      "to_company_domain": "sunrisesenior.com"
    }
  ]
}
```

### Response Format

```json
{
  "catalogued": true,
  "company_id": "550e8400-e29b-41d4-a716-446655440000",
  "contacts": [
    {
      "id": "660e8400-e29b-41d4-a716-446655440001",
      "email": "REMOVED",
      "verification": {
        "status": "valid",
        "is_deliverable": true,
        "is_disposable": false,
        "is_role_based": false,
        "risk_level": "low",
        "score": 95
      }
    },
    {
      "id": "",
      "email": "REMOVED",
      "verification": {
        "status": "invalid",
        "is_deliverable": false,
        "risk_level": "high",
        "score": 0
      }
    }
  ],
  "message": "Verified 2 emails, catalogued 1 valid contacts"
}
```

**Important:** Only contacts with valid, deliverable emails are stored. Invalid emails are verified and rejected.

## Understanding the Response

### Contact Verification Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Contact ID if stored (empty if rejected) |
| `email` | string | Email address |
| `verification.status` | string | `valid`, `invalid`, `disposable`, `syntax_error` |
| `verification.is_deliverable` | bool | Can receive mail |
| `verification.is_disposable` | bool | Temporary/throwaway domain |
| `verification.is_role_based` | bool | Generic address (info@, support@) |
| `verification.risk_level` | string | `low`, `medium`, `high`, `unknown` |
| `verification.score` | int | 0-100 quality score |

### Storage Logic

A contact is stored if ALL conditions are met:
- `status == "valid"`
- `is_deliverable == true`
- `risk_level` is `"low"` or `"medium"`

## Source Information

**Always include source information.** This tracks where data came from:

```json
"source": {
  "app": "your-app-name",      // Required: Your app identifier
  "url": "https://...",        // Recommended: Citation URL
  "record_id": "ABC-123"       // Optional: Your internal record ID
}
```

This enables:
- Data provenance tracking
- Citation for compliance
- Cross-app data enrichment

## Custom Fields

Use `custom_fields` for app-specific data that doesn't fit standard fields:

```json
{
  "company": {
    "name": "Acme Corp",
    "domain": "acme.com",
    "custom_fields": {
      "property_type": "Industrial",
      "square_footage": 50000,
      "listing_id": "LP-12345"
    }
  },
  "contacts": [
    {
      "email": "REMOVED",
      "custom_fields": {
        "license_number": "BRE-123456",
        "specialization": "Industrial"
      }
    }
  ]
}
```

Custom fields are stored in JSONB and can be queried later.

## Connections (Relationships)

Track relationships between entities:

```json
"connections": [
  {
    "relationship_type": "works_at",
    "from_contact_email": "REMOVED",
    "to_company_domain": "acme.com"
  },
  {
    "relationship_type": "owns",
    "from_contact_email": "REMOVED",
    "to_company_domain": "acme.com"
  }
]
```

Common relationship types:
- `works_at` - Employee at company
- `owns` - Owner of company
- `manages` - Manages company/facility
- `represents` - Broker/agent representing

## Lookup Endpoints (Query Data)

### Lookup by Email

```python
GET /api/v1/catalogue/lookup/email/{email}
```

Returns contact with company and source citations.

### Lookup by Domain

```python
GET /api/v1/catalogue/lookup/domain/{domain}
```

Returns company with all contacts.

### Search

```python
POST /api/v1/catalogue/search
{
  "query": "healthcare",
  "state": "CA",
  "industry": "Healthcare",
  "limit": 50
}
```

## Verification-Only Mode

If you only need SMTP verification without cataloguing:

```python
POST /api/v1/verify/email
{
  "email": "REMOVED"
}
```

This verifies without storing data.

## Error Handling

| Code | Meaning |
|------|---------|
| 401 | Invalid API key |
| 403 | Permission denied or usage limit exceeded |
| 429 | Rate limited |
| 500 | Server error |

## Best Practices

1. **Always include source URL** - Helps with data provenance and compliance
2. **Batch submissions** - Send company + all contacts together
3. **Handle rejections gracefully** - Some emails will fail verification
4. **Use custom_fields** - Store app-specific data for later retrieval
5. **Track connections** - Document relationships between entities

## Complete Integration Example

```python
import httpx
from typing import Optional

class CenterDeepVerify:
    """Client for Center Deep Verify API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://verify.centerdeep.online/api/v1"

    async def submit(
        self,
        source_app: str,
        company: Optional[dict] = None,
        contacts: list[dict] = None,
        source_url: str = None,
        connections: list[dict] = None,
    ) -> dict:
        """Submit data for verification and cataloguing."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/catalogue/submit",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "company": company,
                    "contacts": contacts or [],
                    "source": {
                        "app": source_app,
                        "url": source_url,
                    },
                    "connections": connections or [],
                },
            )
            response.raise_for_status()
            return response.json()

    async def verify_single(self, email: str) -> dict:
        """Verify a single email without cataloguing."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/verify/email",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"email": email},
            )
            response.raise_for_status()
            return response.json()

    async def lookup_email(self, email: str) -> dict:
        """Look up catalogued data by email."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/catalogue/lookup/email/{email}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            return response.json()


# Usage
async def process_scraped_data(scraped_company, scraped_contacts, page_url):
    verify = CenterDeepVerify(api_key="sk-cdv-xxxxx")

    result = await verify.submit(
        source_app="my-scraper-app",
        company={
            "name": scraped_company["name"],
            "domain": scraped_company["website"],
            "industry": scraped_company.get("industry"),
            "city": scraped_company.get("city"),
            "state": scraped_company.get("state"),
        },
        contacts=[
            {
                "email": c["email"],
                "first_name": c.get("first_name"),
                "last_name": c.get("last_name"),
                "title": c.get("title"),
                "phone": c.get("phone"),
            }
            for c in scraped_contacts
        ],
        source_url=page_url,
    )

    # Filter to only valid contacts
    valid_contacts = [
        c for c in result["contacts"]
        if c["verification"]["status"] == "valid"
    ]

    return {
        "company_id": result["company_id"],
        "valid_contacts": valid_contacts,
        "rejected_count": len(scraped_contacts) - len(valid_contacts),
    }
```

## Support

- API Docs: https://verify.centerdeep.online/docs
- Dashboard: https://dataintel.centerdeep.online
