# Center Deep Data Intel - Architecture Document

## Overview

Center Deep Data Intel is a standalone service for email verification and enriched data cataloguing. It runs independently but integrates with the Center Deep / Ops-Center ecosystem.

## Core Principles

1. **SMTP Verification Must Work** - Priority #1, everything else is secondary
2. **Apps Keep Their Own Data** - We catalogue copies, don't replace app databases
3. **Platform Agnostic** - Works with SaaS, desktop, mobile apps
4. **Independent Operation** - Can run standalone or integrate with Ops-Center

## Service Ports

| Service | Port | Description |
|---------|------|-------------|
| Backend API | 8501 | FastAPI REST/WebSocket API |
| Frontend | 8502 | React Dashboard |
| OAuth2 Proxy | 8503 | Authentication proxy |

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     CENTER DEEP DATA INTEL                               │
│                    verify.centerdeep.online                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────┐      ┌──────────────────────────────────────┐  │
│  │     FRONTEND        │      │           BACKEND                     │  │
│  │     (React)         │      │          (FastAPI)                    │  │
│  │     Port: 8502      │      │          Port: 8501                   │  │
│  ├─────────────────────┤      ├──────────────────────────────────────┤  │
│  │ • Dashboard         │      │ VERIFICATION:                        │  │
│  │ • Single verify     │      │ • POST /api/v1/verify/email          │  │
│  │ • Batch upload      │      │ • POST /api/v1/verify/batch          │  │
│  │ • CSV upload        │      │ • GET  /api/v1/verify/job/:id        │  │
│  │ • Browse catalogue  │      │                                      │  │
│  │ • Search            │      │ CATALOGUE:                           │  │
│  │ • Stats             │      │ • POST /api/v1/catalogue/submit      │  │
│  └─────────────────────┘      │ • POST /api/v1/catalogue/upload/csv  │  │
│                               │ • POST /api/v1/catalogue/bulk        │  │
│                               │ • GET  /api/v1/catalogue/search      │  │
│                               │ • GET  /api/v1/catalogue/lookup/*    │  │
│                               │                                      │  │
│                               │ STREAMING:                           │  │
│                               │ • WS  /api/v1/stream/verify          │  │
│                               │ • WS  /api/v1/stream/catalogue       │  │
│                               └──────────────────────────────────────┘  │
│                                              │                           │
│  ┌───────────────────────────────────────────┼───────────────────────┐  │
│  │                        DATABASES          │                       │  │
│  ├───────────────────────────────────────────┼───────────────────────┤  │
│  │                                           │                       │  │
│  │  ┌─────────────────┐  ┌─────────────────┐ │ ┌─────────────────┐   │  │
│  │  │   PostgreSQL    │  │     Qdrant      │ │ │     Neo4j       │   │  │
│  │  │   (Required)    │  │   (Optional)    │ │ │   (Optional)    │   │  │
│  │  ├─────────────────┤  ├─────────────────┤ │ ├─────────────────┤   │  │
│  │  │ • Companies     │  │ • Company       │ │ │ • Company→Co.   │   │  │
│  │  │ • Contacts      │  │   embeddings    │ │ │ • Contact→Co.   │   │  │
│  │  │ • Emails        │  │ • Contact       │ │ │ • Contact→Cont. │   │  │
│  │  │ • Verifications │  │   embeddings    │ │ │ • Employment    │   │  │
│  │  │ • Sources       │  │ • Semantic      │ │ │ • Networks      │   │  │
│  │  │ • API Keys      │  │   search        │ │ │                 │   │  │
│  │  └─────────────────┘  └─────────────────┘ │ └─────────────────┘   │  │
│  │   unicorn-postgresql    unicorn-qdrant    │   loopnet-neo4j       │  │
│  │   DB: dataintel_db      Collection: di    │   DB: dataintel       │  │
│  └───────────────────────────────────────────┴───────────────────────┘  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
                    ▼                               ▼
        ┌───────────────────┐           ┌───────────────────┐
        │    AUTH OPTIONS   │           │  OPS-CENTER       │
        ├───────────────────┤           │  INTEGRATION      │
        │ Browser:          │           ├───────────────────┤
        │ • OAuth2 Proxy    │           │ • Menu link       │
        │ • Keycloak SSO    │           │ • Embedded iframe │
        │                   │           │ • Credit tracking │
        │ API:              │           │ • Stats widget    │
        │ • API Keys        │           │                   │
        │ • Keycloak JWT    │           │                   │
        └───────────────────┘           └───────────────────┘
```

## Data Flow

### Flow 1: App Submits Enriched Data + Gets Verification

```
┌──────────────┐     POST /api/v1/catalogue/submit     ┌──────────────┐
│ loopnet-     │ ─────────────────────────────────────→│ Data Intel   │
│ leads        │     {                                 │              │
│              │       company: {...},                 │ 1. Normalize │
│              │       contacts: [{email:...}],        │ 2. Dedupe    │
│              │       source_app: "loopnet",          │ 3. Store     │
│              │       verify_emails: true             │ 4. Verify    │
│              │     }                                 │              │
│              │ ←─────────────────────────────────────│              │
│              │     {                                 │              │
│              │       catalogued: true,               │              │
│              │       verifications: [                │              │
│              │         {email: "...", valid: true}   │              │
│              │       ]                               │              │
│              │     }                                 │              │
└──────────────┘                                       └──────────────┘
```

### Flow 2: Simple SMTP Verification Only

```
┌──────────────┐     POST /api/v1/verify/email         ┌──────────────┐
│ Any App      │ ─────────────────────────────────────→│ Data Intel   │
│              │     {email: "REMOVED"}          │              │
│              │ ←─────────────────────────────────────│ SMTP Check   │
│              │     {                                 │              │
│              │       email: "REMOVED",         │              │
│              │       valid: true,                    │              │
│              │       smtp_verified: true,            │              │
│              │       risk_score: 0.15                │              │
│              │     }                                 │              │
└──────────────┘                                       └──────────────┘
```

### Flow 3: CSV Batch Upload

```
┌──────────────┐     POST /api/v1/catalogue/upload/csv ┌──────────────┐
│ Dashboard    │ ─────────────────────────────────────→│ Data Intel   │
│ User         │     multipart/form-data: file.csv     │              │
│              │ ←─────────────────────────────────────│ Queue Job    │
│              │     {job_id: "uuid", status: "queued"}│              │
│              │                                       │              │
│              │     GET /api/v1/verify/job/:id        │              │
│              │ ─────────────────────────────────────→│              │
│              │ ←─────────────────────────────────────│              │
│              │     {progress: 45%, results: [...]}   │              │
└──────────────┘                                       └──────────────┘
```

## Database Schema (PostgreSQL)

### Core Tables

```sql
-- Catalogued companies
catalogue_companies (
    id, name, primary_domain, industry, location_state,
    employee_count, enrichment_data JSONB, source_apps TEXT[],
    first_seen_at, last_updated_at
)

-- Catalogued contacts
catalogue_contacts (
    id, company_id FK, full_name, title, primary_email,
    phone, linkedin_url, enrichment_data JSONB, source_apps TEXT[],
    first_seen_at, last_updated_at
)

-- Verified emails
catalogue_emails (
    id, contact_id FK, email UNIQUE, domain,
    is_valid, is_verified_smtp, is_catchall, is_disposable,
    risk_score, last_verified_at, verification_count, bounce_count
)

-- Verification jobs (batch processing)
verification_jobs (
    id, status, total_count, processed_count,
    results JSONB, created_by, created_at, completed_at
)

-- Source tracking
catalogue_sources (
    id, entity_type, entity_id, source_app,
    source_record_id, source_url, contributed_data JSONB
)

-- API keys for service auth
api_keys (
    id, key_prefix, key_hash, app_name, tier,
    rate_limit, is_active, last_used_at
)
```

## Authentication

### Browser Users (Dashboard)
- OAuth2 Proxy → Keycloak (uchub realm)
- Same pattern as other Center Deep apps

### Service-to-Service (Apps)
- API Keys: `REMOVED`
- Keycloak JWT (optional): Client credentials flow

### Standalone Mode
- Can run without OAuth2 Proxy for development
- API key auth still works

## API Endpoints

### Verification (Priority #1)

```
POST /api/v1/verify/email
  Body: {email: "REMOVED"}
  Response: {email, valid, smtp_verified, mx_valid, risk_score, checks}

POST /api/v1/verify/batch
  Body: {emails: ["REMOVED", "REMOVED"], async: true}
  Response: {job_id} or {results: [...]}

GET /api/v1/verify/job/:job_id
  Response: {status, progress, results}
```

### Catalogue

```
POST /api/v1/catalogue/submit
  Body: {company, contacts[], source_app, verify_emails: true}
  Response: {catalogued, company_id, contacts[{id, verification}]}

POST /api/v1/catalogue/bulk
  Body: {records: [{company, contacts}...], source_app}
  Response: {job_id, queued_count}

POST /api/v1/catalogue/upload/csv
  Body: multipart/form-data with CSV file
  Response: {job_id, detected_columns, row_count}

GET /api/v1/catalogue/lookup/email/:email
GET /api/v1/catalogue/lookup/domain/:domain
GET /api/v1/catalogue/search?q=...&industry=...&state=...
```

### Streaming

```
WebSocket /api/v1/stream/verify
  Send: {emails: [...]} in chunks
  Receive: {email, result} as processed

WebSocket /api/v1/stream/catalogue
  Send: {records: [...]} in chunks
  Receive: {record_id, status} as processed
```

## Input Methods

| Method | Use Case | Endpoint |
|--------|----------|----------|
| Single | Real-time verification | POST /verify/email |
| Batch | Up to 1000 at once | POST /verify/batch |
| CSV Upload | User uploads file | POST /catalogue/upload/csv |
| Bulk API | App sends many records | POST /catalogue/bulk |
| Streaming | Large datasets | WS /stream/verify |
| Chunked | Mobile/slow connections | Multiple smaller POSTs |

## Ops-Center Integration

### Option A: Menu Link
Add Data Intel to Ops-Center sidebar, opens in new tab.

### Option B: Iframe Embed
Embed Data Intel dashboard in Ops-Center page.

### Option C: Widget
Stats widget showing verification counts, catalogue size.

### Credit Integration
- Report usage to Ops-Center for billing
- Check credits before expensive operations

## File Structure

```
/home/muut/Production/centerdeep-data-intel/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app
│   │   ├── core/
│   │   │   ├── config.py              # Settings
│   │   │   ├── database.py            # PostgreSQL connection
│   │   │   ├── security.py            # Auth middleware
│   │   │   └── redis.py               # Redis for jobs/cache
│   │   ├── services/
│   │   │   ├── smtp_verifier.py       # SMTP verification engine
│   │   │   ├── dns_checker.py         # MX/DNS validation
│   │   │   ├── catalogue_service.py   # Data normalization
│   │   │   └── job_service.py         # Batch job management
│   │   ├── routers/
│   │   │   ├── verify.py              # Verification endpoints
│   │   │   ├── catalogue.py           # Catalogue endpoints
│   │   │   ├── search.py              # Search endpoints
│   │   │   └── health.py              # Health checks
│   │   ├── models/
│   │   │   ├── company.py
│   │   │   ├── contact.py
│   │   │   ├── email.py
│   │   │   └── job.py
│   │   └── schemas/
│   │       ├── verify.py
│   │       ├── catalogue.py
│   │       └── responses.py
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
├── docker-compose.yml                  # Development
├── docker-compose.prod.yml             # Production with OAuth2
├── .env.example
├── CLAUDE.md                           # AI assistant context
└── ARCHITECTURE.md                     # This file
```

## Deployment

### Development (standalone)
```bash
docker compose up -d
# Access at http://localhost:8502
# API at http://localhost:8501
```

### Production (with SSO)
```bash
docker compose -f docker-compose.prod.yml up -d
# Access at https://verify.centerdeep.online
```

## Future Enhancements

### Vector Search (Qdrant)
- Embed company descriptions
- "Find similar companies" semantic search

### Graph Database (Neo4j)
- Company relationship networks
- Contact connection graphs
- Employment history traversal

### ML Risk Scoring
- Train on bounce/success data
- Predict deliverability beyond SMTP

### Ops-Center Deep Integration
- Native credit deduction
- Usage analytics dashboard
- Organization-level data isolation
