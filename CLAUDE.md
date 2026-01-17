# CLAUDE.md - AI Assistant Context for Center Deep Data Intel

## Project Overview

**Name:** Center Deep Data Intel
**Purpose:** Email verification and enriched data cataloguing service
**Ecosystem:** Part of Center Deep / Unicorn Commander platform

This is a standalone microservice that provides:
1. **SMTP Email Verification** - Verify email deliverability via DNS/MX/SMTP checks
2. **Phone Number Verification** - Validate phone numbers using Google's libphonenumber with optional carrier lookup
3. **Data Cataloguing** - Store and normalize enriched company/contact data from various apps
4. **Unified Lookup** - Query catalogued data by email, domain, or full-text search
5. **Source Tracking** - Track which applications contributed data to each entity

**Key Feature:** Contacts are stored if they have a valid email OR a valid phone number. This ensures valuable contacts with only phone numbers are not discarded.

## Tech Stack

### Backend
- **Framework:** FastAPI (async Python web framework)
- **ORM:** SQLAlchemy 2.0 with async support
- **Database Driver:** asyncpg (async PostgreSQL)
- **Validation:** Pydantic v2
- **Logging:** structlog (structured JSON logging)
- **HTTP Client:** httpx (async)

### Frontend
- **Framework:** React 18
- **Build Tool:** Vite
- **Styling:** Tailwind CSS
- **State Management:** React Query / Zustand

### Infrastructure
- **Database:** PostgreSQL (unicorn-postgresql, database: `dataintel_db`)
- **Cache/Queue:** Redis (unicorn-redis, database: 5)
- **Authentication:** OAuth2 Proxy + Keycloak (realm: `uchub`)
- **Container:** Docker / Docker Compose

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │     CENTER DEEP DATA INTEL          │
                    │     verify.centerdeep.online        │
                    └─────────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│   Frontend    │         │   Backend     │         │   OAuth2      │
│   (React)     │         │   (FastAPI)   │         │   Proxy       │
│   Port: 8502  │         │   Port: 8501  │         │   Port: 8503  │
└───────────────┘         └───────────────┘         └───────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
            ┌───────────┐   ┌───────────┐   ┌───────────┐
            │ PostgreSQL│   │   Redis   │   │  Keycloak │
            │ dataintel │   │   DB: 5   │   │   uchub   │
            └───────────┘   └───────────┘   └───────────┘
```

### Integration Flow

Apps (loopnet-leads, ca-retirement, etc.) integrate by:
1. Enriching data locally (scraping, API calls, etc.)
2. Calling `POST /api/v1/catalogue/submit` with enriched data
3. Optionally requesting email verification in the same call
4. Receiving verification status immediately or via job polling

## Key APIs

### Email Verification

```bash
# Single email verification
POST /api/v1/verify/email
Content-Type: application/json
REMOVED

{"email": "REMOVED"}

# Response
{
  "email": "REMOVED",
  "status": "valid",
  "is_deliverable": true,
  "is_disposable": false,
  "is_role_based": false,
  "risk_level": "low",
  "score": 95
}
```

### Phone Verification

```bash
# Single phone verification
POST /api/v1/verify/phone
Content-Type: application/json
REMOVED

{
  "phone": "+1 (843) 555-1234",
  "region": "US",
  "use_api": true
}

# Response
{
  "phone": "+1 (843) 555-1234",
  "formatted_e164": "+18435551234",
  "formatted_national": "(843) 555-1234",
  "is_valid": true,
  "is_possible": true,
  "phone_type": "mobile",
  "carrier_name": "Verizon Wireless",
  "country_code": "US",
  "region": "South Carolina",
  "timezone": ["America/New_York"],
  "risk_level": "low",
  "score": 85,
  "verification_method": "phonenumbers"
}
```

```bash
# Batch phone verification (local-only by default for speed)
POST /api/v1/verify/phone/batch
Content-Type: application/json

{
  "phones": ["+1 (843) 555-1234", "+1 (843) 555-5678"],
  "region": "US",
  "use_api": false
}
```

**Phone Verification Stack:**
- **phonenumbers** (Google's libphonenumber) - Format, type, carrier patterns (free, local)
- **Veriphone API** - Enhanced carrier lookup (1,000/month free, optional)
- **phone-number-api.com** - Backup lookup (free, no API key needed)

```bash
# Batch verification
POST /api/v1/verify/batch
Content-Type: application/json

{
  "emails": ["REMOVED", "REMOVED"],
  "async": true
}

# Response (async)
{"job_id": "uuid-here"}

# Check job status
GET /api/v1/verify/job/{job_id}
```

### Data Cataloguing

```bash
# Submit enriched data
POST /api/v1/catalogue/submit
Content-Type: application/json

{
  "company": {
    "name": "Acme Corp",
    "domain": "acme.com",
    "industry": "Technology"
  },
  "contacts": [
    {
      "email": "REMOVED",
      "name": "John Doe",
      "title": "CTO"
    }
  ],
  "source_app": "loopnet-leads",
  "verify_emails": true
}

# Response
{
  "catalogued": true,
  "company_id": "uuid",
  "contacts": [
    {
      "id": "uuid",
      "email": "REMOVED",
      "verification": {"status": "valid", "score": 95}
    }
  ]
}
```

```bash
# Bulk submission
POST /api/v1/catalogue/bulk
{
  "records": [{...}, {...}],
  "source_app": "app-name",
  "async": true
}

# CSV upload
POST /api/v1/catalogue/upload/csv
Content-Type: multipart/form-data
file=@contacts.csv
```

### Data Lookup

```bash
# Lookup by email
GET /api/v1/catalogue/lookup/email/REMOVED

# Lookup by domain
GET /api/v1/catalogue/lookup/domain/acme.com

# Search
GET /api/v1/catalogue/search?q=technology&industry=Software&state=CA&limit=50

# Statistics
GET /api/v1/catalogue/stats
```

## Database Tables

### Core Tables

| Table | Purpose |
|-------|---------|
| `catalogue_companies` | Company records with enrichment JSONB |
| `catalogue_contacts` | Contact records linked to companies |
| `catalogue_emails` | Email verification records and status |
| `verification_jobs` | Async job tracking for batch operations |
| `catalogue_sources` | Data source/provenance tracking |
| `api_keys` | API key authentication records |

### Key Relationships

```
catalogue_companies (1) ──────< (N) catalogue_contacts
                     │
catalogue_contacts (1) ──────< (N) catalogue_emails
                     │
catalogue_sources ────────────────> [company | contact | email]
```

### Important Columns

- **enrichment_data (JSONB):** Flexible storage for additional fields from various sources
- **source_apps (TEXT[]):** Array tracking which apps contributed data
- **primary_domain:** Unique index for company deduplication
- **primary_email:** Unique index for contact deduplication

## Authentication

### Browser Access (Dashboard)

1. User visits `https://verify.centerdeep.online`
2. OAuth2 Proxy intercepts, redirects to Keycloak
3. User authenticates with SSO (uchub realm)
4. OAuth2 Proxy sets `X-Forwarded-User` header
5. Backend accepts request as authenticated

### API Access

```bash
# Option 1: API Key (recommended for apps)
curl -H "REMOVED" \
     https://verify.centerdeep.online/api/v1/verify/email

# Option 2: Keycloak JWT
curl -H "Authorization: Bearer <jwt-token>" \
     https://verify.centerdeep.online/api/v1/verify/email
```

### Development Mode

Set `STANDALONE_MODE=true` to bypass authentication for local development:

```bash
# .env
STANDALONE_MODE=true
```

## Integration Guide

### For Apps Sending Data

```python
import httpx

async def submit_to_catalogue(company_data, contacts, source_app):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://verify.centerdeep.online/api/v1/catalogue/submit",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "company": company_data,
                "contacts": contacts,
                "source_app": source_app,
                "verify_emails": True,
            },
        )
        return response.json()
```

### Expected Flow

1. App scrapes/enriches company data
2. App calls `/catalogue/submit` with `verify_emails=true`
3. Data Intel normalizes, deduplicates, stores
4. Emails are verified via SMTP
5. App receives verification results immediately
6. Data is available for lookup/search across all apps

## Common Commands

### Development

```bash
# Start all services
cd /home/muut/Production/centerdeep-data-intel
docker compose up -d

# View logs
docker compose logs -f backend

# Restart backend after code changes
docker compose restart backend

# Access database
docker exec -it unicorn-postgresql psql -U unicorn -d dataintel_db
```

### Production

```bash
# Deploy with OAuth2 Proxy
docker compose -f docker-compose.prod.yml up -d

# Check service health
curl https://verify.centerdeep.online/health

# View production logs
docker compose -f docker-compose.prod.yml logs -f backend
```

### Database Operations

```bash
# Connect to PostgreSQL
docker exec -it unicorn-postgresql psql -U unicorn -d dataintel_db

# Common queries
SELECT COUNT(*) FROM catalogue_companies;
SELECT COUNT(*) FROM catalogue_contacts;
SELECT COUNT(*) FROM catalogue_emails WHERE is_verified_smtp = true;

# View sources by app
SELECT source_app, COUNT(*) FROM catalogue_sources GROUP BY source_app;
```

### Redis Operations

```bash
# Connect to Redis
docker exec -it unicorn-redis redis-cli -n 5

# View jobs
KEYS job:*
GET job:{job_id}
```

## File Structure

```
/home/muut/Production/centerdeep-data-intel/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI application entry
│   │   ├── core/
│   │   │   ├── config.py              # Settings (env vars)
│   │   │   ├── database.py            # Async SQLAlchemy setup
│   │   │   ├── security.py            # Auth middleware
│   │   │   └── redis.py               # Redis connection
│   │   ├── services/
│   │   │   ├── smtp_verifier.py       # Email verification engine
│   │   │   ├── catalogue_service.py   # Data normalization/dedup
│   │   │   ├── disposable_domains.py  # Disposable email list
│   │   │   └── job_service.py         # Background job handling
│   │   ├── routers/
│   │   │   ├── verify.py              # /api/v1/verify/*
│   │   │   ├── catalogue.py           # /api/v1/catalogue/*
│   │   │   └── health.py              # /health endpoints
│   │   ├── models/
│   │   │   ├── company.py             # SQLAlchemy Company model
│   │   │   ├── contact.py             # SQLAlchemy Contact model
│   │   │   ├── email.py               # SQLAlchemy Email model
│   │   │   ├── job.py                 # SQLAlchemy Job model
│   │   │   └── source.py              # SQLAlchemy Source model
│   │   └── schemas/
│   │       ├── verify.py              # Verification Pydantic models
│   │       └── catalogue.py           # Catalogue Pydantic models
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── VerifyPage.tsx
│   │   │   ├── CataloguePage.tsx
│   │   │   └── UploadPage.tsx
│   │   ├── components/
│   │   └── App.tsx
│   ├── Dockerfile.prod
│   └── package.json
├── docker-compose.yml                  # Development configuration
├── docker-compose.prod.yml             # Production with OAuth2 Proxy
├── .env.example
├── CLAUDE.md                           # This file
└── ARCHITECTURE.md                     # Detailed architecture docs
```

## Production URLs

| Service | URL |
|---------|-----|
| Dashboard | https://verify.centerdeep.online |
| API Base | https://verify.centerdeep.online/api/v1 |
| API Docs | https://verify.centerdeep.online/docs |
| Health Check | https://verify.centerdeep.online/health |

## Environment Variables

```bash
# Application
APP_NAME="Center Deep Data Intel"
APP_VERSION="1.0.0"
DEBUG=false
LOG_LEVEL=INFO

# Database
DATABASE_URL=postgresql+asyncpg://unicorn:password@unicorn-postgresql:5432/dataintel_db

# Redis
REDIS_URL=redis://unicorn-redis:6379/5

# Security
STANDALONE_MODE=false  # Set true for dev without auth
API_KEY_HEADER=Authorization

# Keycloak (production)
KEYCLOAK_REALM_URL=https://auth.centerdeep.online/realms/uchub
KEYCLOAK_CLIENT_ID=data-intel

# SMTP Verification
SMTP_TIMEOUT=10
SMTP_CONCURRENCY=10
```

## Code Patterns

### Adding a New Router

```python
# backend/app/routers/new_router.py
from fastapi import APIRouter, Depends
from app.core.security import AppContext, get_current_app

router = APIRouter(prefix="/api/v1/new", tags=["New Feature"])

@router.get("/endpoint")
async def endpoint(app: AppContext = Depends(get_current_app)):
    return {"message": "Hello"}
```

```python
# Update backend/app/routers/__init__.py
from app.routers.new_router import router as new_router
__all__ = [..., "new_router"]

# Update backend/app/main.py
from app.routers import new_router
app.include_router(new_router)
```

### Adding a New Model

```python
# backend/app/models/new_model.py
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class NewModel(Base):
    __tablename__ = "new_table"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # ... fields
```

### Database Session Pattern

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from fastapi import Depends

@router.get("/items")
async def get_items(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Item))
    return result.scalars().all()
```

## Troubleshooting

### Common Issues

**Database connection fails:**
```bash
# Check PostgreSQL is running
docker ps | grep postgresql
# Check connection string in .env
```

**Authentication errors in production:**
```bash
# Check OAuth2 Proxy logs
docker compose -f docker-compose.prod.yml logs oauth2-proxy
# Verify Keycloak realm configuration
```

**SMTP verification timeouts:**
```bash
# Check DNS resolution
nslookup gmail.com
# Increase timeout in .env
SMTP_TIMEOUT=30
```

**Redis connection issues:**
```bash
# Check Redis is running
docker exec -it unicorn-redis redis-cli ping
# Verify database number (should be 5)
```

## Related Projects

- **Ops-Center:** Main dashboard that links to Data Intel
- **loopnet-leads:** Commercial real estate lead scraper
- **ca-retirement:** California retirement facility data
- **Unicorn Commander:** Umbrella project for all Center Deep services

## Support

For issues or questions about this service, check:
1. This CLAUDE.md file
2. ARCHITECTURE.md for detailed diagrams
3. API documentation at /docs endpoint
4. Logs via `docker compose logs -f backend`
