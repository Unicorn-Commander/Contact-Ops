# Data Intel Developer Integration Guide

This guide explains how to integrate your application with Center Deep Data Intel for email verification and data cataloguing.

## Overview

Data Intel provides two main capabilities for your apps:

1. **Email Verification** - Verify emails before sending or storing
2. **Data Cataloguing** - Submit enriched company/contact data to the central database

## Getting an API Key

### Option 1: Via Ops-Center Admin

1. Go to Ops-Center → Settings → Data Intel
2. Click "API Keys" tab
3. Click "Create API Key"
4. Enter your app name and settings
5. **Save the key immediately** - it's only shown once!

### Option 2: Via API

```bash
curl -X POST https://verify.centerdeep.online/api/v1/admin/api-keys \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "my-app",
    "description": "My application integration",
    "tier": "standard",
    "rate_limit": 1000
  }'
```

Response includes the API key (shown once):
```json
{
  "id": "uuid",
  "key": "sk-my-app-abc123...",
  "warning": "Save this key now. It cannot be retrieved again."
}
```

## Authentication

Include your API key in all requests:

```bash
REMOVED
```

Or use the header:
```bash
X-API-Key: sk-your-api-key-here
```

## API Base URL

| Environment | URL |
|-------------|-----|
| Production | https://verify.centerdeep.online |
| Development | http://localhost:8501 |
| Docker Network | http://dataintel-backend:8501 |

---

## Email Verification API

### Single Email Verification

**Endpoint:** `POST /api/v1/verify/email`

**Request:**
```json
{
  "email": "REMOVED"
}
```

**Response:**
```json
{
  "email": "REMOVED",
  "status": "valid",
  "is_deliverable": true,
  "is_disposable": false,
  "is_role_based": false,
  "is_catch_all": false,
  "is_free_provider": false,
  "risk_level": "low",
  "domain": "example.com",
  "mx_records": [
    {"host": "mail.example.com", "priority": 10}
  ],
  "score": 95,
  "verified_at": "2024-01-15T10:30:00Z"
}
```

### Batch Verification

**Endpoint:** `POST /api/v1/verify/batch`

**Request:**
```json
{
  "emails": [
    "REMOVED",
    "REMOVED",
    "REMOVED"
  ],
  "async": false
}
```

**Synchronous Response:**
```json
{
  "results": [
    {"email": "REMOVED", "status": "valid", "score": 95},
    {"email": "REMOVED", "status": "valid", "score": 90},
    {"email": "REMOVED", "status": "disposable", "score": 30}
  ],
  "summary": {
    "total": 3,
    "valid": 2,
    "invalid": 0,
    "disposable": 1
  }
}
```

**Async Request (for large batches):**
```json
{
  "emails": ["...hundreds of emails..."],
  "async": true
}
```

**Async Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "total_count": 500
}
```

**Check Job Status:**
```bash
GET /api/v1/verify/job/{job_id}
```

---

## Data Cataloguing API

### Submit Company and Contacts

**Endpoint:** `POST /api/v1/catalogue/submit`

This is the main endpoint for apps to submit enriched data.

**Request:**
```json
{
  "company": {
    "name": "Acme Corporation",
    "domain": "acme.com",
    "industry": "Technology",
    "sub_industry": "SaaS",
    "headquarters_city": "San Francisco",
    "headquarters_state": "CA",
    "employee_count": "50-100",
    "revenue_range": "$10M-$50M",
    "enrichment_data": {
      "linkedin_url": "https://linkedin.com/company/acme",
      "founded_year": 2015,
      "custom_field": "any additional data"
    }
  },
  "contacts": [
    {
      "email": "REMOVED",
      "name": "John Doe",
      "title": "CEO",
      "phone": "REMOVED",
      "linkedin_url": "https://linkedin.com/in/johndoe"
    },
    {
      "email": "REMOVED",
      "name": "Jane Smith",
      "title": "CTO"
    }
  ],
  "source_app": "your-app-name",
  "verify_emails": true
}
```

**Response:**
```json
{
  "catalogued": true,
  "company_id": "550e8400-e29b-41d4-a716-446655440000",
  "contacts": [
    {
      "id": "uuid-1",
      "email": "REMOVED",
      "verification": {
        "status": "valid",
        "score": 95
      }
    },
    {
      "id": "uuid-2",
      "email": "REMOVED",
      "verification": {
        "status": "valid",
        "score": 92
      }
    }
  ]
}
```

### Bulk Submit

**Endpoint:** `POST /api/v1/catalogue/bulk`

For submitting many records at once:

```json
{
  "records": [
    {
      "company": {"name": "Company A", "domain": "a.com"},
      "contacts": [{"email": "REMOVED", "name": "User A"}]
    },
    {
      "company": {"name": "Company B", "domain": "b.com"},
      "contacts": [{"email": "REMOVED", "name": "User B"}]
    }
  ],
  "source_app": "your-app-name",
  "verify_emails": true,
  "async": true
}
```

### CSV Upload

**Endpoint:** `POST /api/v1/catalogue/upload/csv`

```bash
curl -X POST https://verify.centerdeep.online/api/v1/catalogue/upload/csv \
  -H "REMOVED" \
  -F "file=@contacts.csv" \
  -F "source_app=your-app-name" \
  -F "verify_emails=true"
```

CSV should have columns: `email`, `name`, `company`, `title`, etc.

---

## Lookup API

### Lookup by Email

**Endpoint:** `GET /api/v1/catalogue/lookup/email/{email}`

```bash
curl https://verify.centerdeep.online/api/v1/catalogue/lookup/email/REMOVED
```

### Lookup by Domain

**Endpoint:** `GET /api/v1/catalogue/lookup/domain/{domain}`

```bash
curl https://verify.centerdeep.online/api/v1/catalogue/lookup/domain/acme.com
```

### Search

**Endpoint:** `GET /api/v1/catalogue/search`

Query parameters:
- `q` - Search query (name, domain, etc.)
- `industry` - Filter by industry
- `state` - Filter by state
- `source_app` - Filter by source app
- `limit` - Results per page (default: 50)
- `offset` - Pagination offset

```bash
curl "https://verify.centerdeep.online/api/v1/catalogue/search?q=technology&state=CA&limit=20"
```

---

## Code Examples

### Python

```python
import httpx

class DataIntelClient:
    def __init__(self, api_key: str, base_url: str = "https://verify.centerdeep.online"):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {api_key}"}

    async def verify_email(self, email: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/v1/verify/email",
                headers=self.headers,
                json={"email": email}
            )
            response.raise_for_status()
            return response.json()

    async def submit_data(
        self,
        company: dict,
        contacts: list,
        source_app: str,
        verify_emails: bool = True
    ) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/v1/catalogue/submit",
                headers=self.headers,
                json={
                    "company": company,
                    "contacts": contacts,
                    "source_app": source_app,
                    "verify_emails": verify_emails
                }
            )
            response.raise_for_status()
            return response.json()

# Usage
client = DataIntelClient("sk-your-api-key")

# Verify email
result = await client.verify_email("REMOVED")
print(f"Valid: {result['is_deliverable']}, Score: {result['score']}")

# Submit data
result = await client.submit_data(
    company={"name": "Acme Corp", "domain": "acme.com"},
    contacts=[{"email": "REMOVED", "name": "John Doe", "title": "CEO"}],
    source_app="my-app"
)
```

### JavaScript/TypeScript

```typescript
class DataIntelClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(apiKey: string, baseUrl = 'https://verify.centerdeep.online') {
    this.apiKey = apiKey;
    this.baseUrl = baseUrl;
  }

  private async request(endpoint: string, options: RequestInit = {}) {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.apiKey}`,
        ...options.headers,
      },
    });

    if (!response.ok) {
      throw new Error(`API Error: ${response.statusText}`);
    }

    return response.json();
  }

  async verifyEmail(email: string) {
    return this.request('/api/v1/verify/email', {
      method: 'POST',
      body: JSON.stringify({ email }),
    });
  }

  async submitData(company: object, contacts: object[], sourceApp: string, verifyEmails = true) {
    return this.request('/api/v1/catalogue/submit', {
      method: 'POST',
      body: JSON.stringify({
        company,
        contacts,
        source_app: sourceApp,
        verify_emails: verifyEmails,
      }),
    });
  }
}

// Usage
const client = new DataIntelClient('sk-your-api-key');

// Verify email
const result = await client.verifyEmail('REMOVED');
console.log(`Valid: ${result.is_deliverable}, Score: ${result.score}`);

// Submit data
const catalogResult = await client.submitData(
  { name: 'Acme Corp', domain: 'acme.com' },
  [{ email: 'REMOVED', name: 'John Doe', title: 'CEO' }],
  'my-app'
);
```

---

## Best Practices

### 1. Verify Before Storing

```python
# Bad: Store first, verify later
contact = save_contact(email)
verify_later(contact.email)

# Good: Verify first, then store
result = await dataintel.verify_email(email)
if result['score'] >= 70:
    save_contact(email, verification=result)
```

### 2. Always Include source_app

This helps track data provenance:

```python
await client.submit_data(
    company=data,
    contacts=contacts,
    source_app="loopnet-leads"  # Always include this!
)
```

### 3. Use Batch Verification for Large Lists

```python
# Bad: One at a time
for email in emails:
    await client.verify_email(email)

# Good: Batch
results = await client.verify_batch(emails)
```

### 4. Handle Rate Limits

```python
import asyncio

async def verify_with_backoff(email, retries=3):
    for attempt in range(retries):
        try:
            return await client.verify_email(email)
        except RateLimitError:
            await asyncio.sleep(2 ** attempt)
    raise Exception("Rate limit exceeded")
```

---

## Error Handling

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad request (invalid input) |
| 401 | Unauthorized (invalid API key) |
| 404 | Not found |
| 429 | Rate limit exceeded |
| 500 | Server error |

### Error Response Format

```json
{
  "detail": "Error message here",
  "type": "ValidationError"
}
```

---

## Rate Limits

| Tier | Requests/Hour |
|------|---------------|
| Standard | 1,000 |
| Premium | 10,000 |
| Unlimited | No limit |

---

## Interactive API Docs

Full API documentation with try-it-out functionality:

- **Swagger UI**: https://verify.centerdeep.online/docs
- **ReDoc**: https://verify.centerdeep.online/redoc

---

## Support

- **Health Check**: GET https://verify.centerdeep.online/health
- **Admin API Info**: GET https://verify.centerdeep.online/api/v1/admin/info
- **Issues**: Contact Center Deep administrator
