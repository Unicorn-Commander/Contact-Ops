# Data Intel Multi-Tenant Architecture Plan

**Date**: January 15, 2026
**Status**: PROPOSED - Pending Implementation

---

## Executive Summary

This plan addresses the need for:
1. **User-based API keys** tied to subscriptions
2. **RBAC** - Different tiers get different features
3. **Per-app data isolation** while maintaining efficiency
4. **Future searchability** without major refactoring

---

## Architecture Decision: Hybrid Approach

After researching [current best practices](https://www.thenile.dev/blog/multi-tenant-rls), we'll use a **hybrid model**:

| Approach | What We Use It For |
|----------|-------------------|
| **Shared Tables + RLS** | Core entities (companies, contacts, emails) |
| **Source App Column** | Track data provenance, enable per-app access |
| **PostgreSQL RLS** | Enforce access control at database level |
| **Per-App Views** | Enable future per-app search without schema changes |

### Why NOT Separate Databases Per App

- **Operational overhead**: Managing N databases vs 1
- **Cross-app queries**: Would need federation layer
- **Connection pooling**: Each DB needs separate pool
- **Backups/migrations**: Multiply by N apps

### Why NOT Schema-Per-App (Yet)

- **Premature optimization**: Can add later if needed
- **Shared entities**: Companies exist across apps
- **Simpler start**: RLS + source_app column achieves isolation

### What We CAN Add Later

- Per-app schemas for app-specific extension fields
- Cross-app materialized views
- Dedicated read replicas per app
- Full database separation for enterprise clients

---

## Data Model

### Core Tables (Shared, RLS-Protected)

```sql
-- All core tables get these columns:
-- source_app: Which app submitted the data
-- owner_user_id: Which user owns the data
-- created_at, updated_at: Timestamps

catalogue_companies (
    id UUID PRIMARY KEY,
    source_app VARCHAR(50) NOT NULL,      -- 'loopnet-leads', 'ca-retirement', etc.
    owner_user_id UUID NOT NULL,           -- Keycloak user who submitted
    name VARCHAR(255),
    domain VARCHAR(255),
    -- ... other fields
    UNIQUE(domain, source_app)             -- No dupes within same app
);

catalogue_contacts (
    id UUID PRIMARY KEY,
    source_app VARCHAR(50) NOT NULL,
    owner_user_id UUID NOT NULL,
    company_id UUID REFERENCES catalogue_companies,
    email VARCHAR(255),
    -- ... other fields
    UNIQUE(email, source_app)              -- No dupes within same app
);

catalogue_emails (
    id UUID PRIMARY KEY,
    source_app VARCHAR(50) NOT NULL,
    owner_user_id UUID NOT NULL,
    email VARCHAR(255) NOT NULL,
    -- verification fields
    -- ... other fields
);
```

### User & Subscription Tables

```sql
-- API Keys (enhanced)
api_keys (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,                 -- Keycloak user ID
    key_hash VARCHAR(255) NOT NULL,        -- bcrypt hash
    key_prefix VARCHAR(50) NOT NULL,       -- 'sk-username-...'
    app_name VARCHAR(100),                 -- Optional: which app uses this key
    source_apps TEXT[],                    -- Which source_apps this key can access
    permissions TEXT[],                    -- Granted permissions
    is_active BOOLEAN DEFAULT true,
    expires_at TIMESTAMP,
    last_used_at TIMESTAMP,
    request_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Usage Tracking
usage_records (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    api_key_id UUID REFERENCES api_keys,
    action VARCHAR(50) NOT NULL,           -- 'verify:single', 'verify:batch', etc.
    count INTEGER DEFAULT 1,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, action, period_start)
);
```

---

## RBAC Model

### Subscription Tiers

| Tier | Monthly Price | Verifications | Features |
|------|---------------|---------------|----------|
| **Trial** | Free (7 days) | 100 total | verify:single |
| **Basic** | $19 | 1,000/month | verify:single, verify:batch, catalogue:submit |
| **Pro** | $49 | 10,000/month | All Basic + catalogue:search:own, catalogue:lookup |
| **Enterprise** | $99 | Unlimited | All Pro + catalogue:search:cross, admin:stats |

### Permissions

```python
PERMISSIONS = {
    # Verification
    "verify:single": "Verify single email",
    "verify:batch": "Verify batch of emails",

    # Catalogue - Write
    "catalogue:submit": "Submit company/contact data",
    "catalogue:upload": "Upload CSV files",

    # Catalogue - Read (own data)
    "catalogue:search:own": "Search own submitted data",
    "catalogue:lookup:own": "Lookup by email/domain (own data)",

    # Catalogue - Read (cross-app)
    "catalogue:search:cross": "Search across all accessible apps",
    "catalogue:lookup:cross": "Lookup across all accessible apps",

    # Admin
    "admin:keys": "Manage own API keys",
    "admin:stats": "View own usage statistics",
}

TIER_PERMISSIONS = {
    "trial": ["verify:single"],
    "basic": ["verify:single", "verify:batch", "catalogue:submit", "admin:keys"],
    "pro": ["verify:single", "verify:batch", "catalogue:submit", "catalogue:upload",
            "catalogue:search:own", "catalogue:lookup:own", "admin:keys", "admin:stats"],
    "enterprise": ["verify:single", "verify:batch", "catalogue:submit", "catalogue:upload",
                   "catalogue:search:own", "catalogue:lookup:own",
                   "catalogue:search:cross", "catalogue:lookup:cross",
                   "admin:keys", "admin:stats"],
}
```

### Tier Limits

```python
TIER_LIMITS = {
    "trial": {"verifications_per_period": 100, "period_days": 7},
    "basic": {"verifications_per_period": 1000, "period_days": 30},
    "pro": {"verifications_per_period": 10000, "period_days": 30},
    "enterprise": {"verifications_per_period": None, "period_days": 30},  # Unlimited
}
```

---

## Row-Level Security (RLS)

### Enable RLS on Core Tables

```sql
-- Enable RLS
ALTER TABLE catalogue_companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalogue_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalogue_emails ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see their own data
CREATE POLICY user_isolation_policy ON catalogue_companies
    FOR ALL
    USING (owner_user_id = current_setting('app.current_user_id')::UUID);

-- Policy: Users with cross-app permission can see all data they have access to
CREATE POLICY cross_app_policy ON catalogue_companies
    FOR SELECT
    USING (
        source_app = ANY(string_to_array(current_setting('app.accessible_apps'), ','))
    );
```

### Setting Context Per Request

```python
# In FastAPI, before each query:
async def set_db_context(db: AsyncSession, user_id: str, accessible_apps: list[str]):
    await db.execute(text(f"SET app.current_user_id = '{user_id}'"))
    await db.execute(text(f"SET app.accessible_apps = '{','.join(accessible_apps)}'"))
```

---

## Authentication Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    API REQUEST FLOW                              │
└─────────────────────────────────────────────────────────────────┘

1. Request arrives with: REMOVED

2. Validate API Key:
   ├─ Hash the key
   ├─ Look up in api_keys table
   ├─ Check is_active = true
   └─ Check expires_at > now()

3. Get User Subscription:
   ├─ Get user_id from api_key record
   ├─ Query Keycloak for subscription_tier attribute
   │   OR Query Lago for active subscription
   └─ Get tier (trial/basic/pro/enterprise)

4. Check Subscription Status:
   ├─ If no subscription → 401 "Subscription required"
   ├─ If expired → 401 "Subscription expired"
   └─ If active → Continue

5. Check Permission:
   ├─ Get required permission for endpoint
   ├─ Check TIER_PERMISSIONS[tier] includes permission
   └─ If not → 403 "Upgrade required for this feature"

6. Check Usage Limits:
   ├─ Get current period usage from usage_records
   ├─ Compare against TIER_LIMITS[tier]
   └─ If exceeded → 429 "Rate limit exceeded, resets on {date}"

7. Execute Request:
   ├─ Set RLS context (user_id, accessible_apps)
   ├─ Execute query
   ├─ Increment usage counter
   └─ Return response
```

---

## Access Control Matrix

### Who Can Access What

| Actor | GUI Access | API Access | Data Access |
|-------|------------|------------|-------------|
| **You (Admin)** | Full Ops-Center + Data Intel UI | All endpoints | All data |
| **Trial User** | None (API only) | verify:single | None |
| **Basic User** | None (API only) | verify + submit | Own submitted data (write only) |
| **Pro User** | None (API only) | verify + submit + search own | Own app's data (read/write) |
| **Enterprise** | Optional dashboard | All endpoints | Cross-app data |

### GUI Access Control

```
Data Intel Frontend (verify.centerdeep.online):
├─ Public pages: /docs, /health
├─ Admin pages: Require admin role
│   ├─ Dashboard: Admin only
│   ├─ API Keys management: Admin only
│   ├─ Stats: Admin only
│   └─ Settings: Admin only
└─ User pages: Future (if we add user dashboard)

Ops-Center Plugin (/admin/integrations/data-intel):
├─ Requires: logged into Ops-Center
├─ Requires: admin role OR data-intel:admin permission
└─ Shows: Stats, API keys, settings, activity
```

---

## Implementation Phases

### Phase 1: Core Auth & RBAC (Priority)
- [ ] Add user_id to api_keys table
- [ ] Implement proper API key validation with DB lookup
- [ ] Add subscription status check (Keycloak/Lago)
- [ ] Implement permission checking per endpoint
- [ ] Add usage tracking and limits

### Phase 2: Data Isolation
- [ ] Add owner_user_id to catalogue tables
- [ ] Implement RLS policies
- [ ] Update catalogue endpoints to set RLS context
- [ ] Test data isolation

### Phase 3: Search Access Control
- [ ] Implement catalogue:search:own permission
- [ ] Implement catalogue:search:cross permission
- [ ] Add accessible_apps to API key management
- [ ] Build search endpoints with proper filtering

### Phase 4: User Dashboard (Future)
- [ ] User-facing API key management
- [ ] Usage dashboard
- [ ] Subscription management integration

---

## Database Migration Plan

```sql
-- Migration 001: Add user tracking to api_keys
ALTER TABLE api_keys ADD COLUMN user_id UUID;
ALTER TABLE api_keys ADD COLUMN source_apps TEXT[];
ALTER TABLE api_keys ADD COLUMN permissions TEXT[];

-- Migration 002: Add owner tracking to catalogue tables
ALTER TABLE catalogue_companies ADD COLUMN owner_user_id UUID;
ALTER TABLE catalogue_contacts ADD COLUMN owner_user_id UUID;
ALTER TABLE catalogue_emails ADD COLUMN owner_user_id UUID;

-- Migration 003: Create usage tracking table
CREATE TABLE usage_records (...);

-- Migration 004: Enable RLS
ALTER TABLE catalogue_companies ENABLE ROW LEVEL SECURITY;
-- ... policies
```

---

## API Changes

### New Response Headers

```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 847
X-RateLimit-Reset: 2026-02-01T00:00:00Z
X-Subscription-Tier: pro
```

### New Error Responses

```json
// 401 - Subscription Required
{
  "detail": "Active subscription required",
  "code": "SUBSCRIPTION_REQUIRED",
  "upgrade_url": "https://unicorncommander.ai/plans"
}

// 403 - Permission Denied
{
  "detail": "This feature requires Pro tier or higher",
  "code": "PERMISSION_DENIED",
  "required_permission": "catalogue:search:own",
  "current_tier": "basic",
  "upgrade_url": "https://unicorncommander.ai/plans"
}

// 429 - Rate Limited
{
  "detail": "Monthly verification limit exceeded",
  "code": "RATE_LIMITED",
  "limit": 1000,
  "used": 1000,
  "resets_at": "2026-02-01T00:00:00Z",
  "upgrade_url": "https://unicorncommander.ai/plans"
}
```

---

## Integration Points

### Keycloak
- User ID: `sub` claim
- Subscription tier: `subscription_tier` attribute
- Subscription status: `subscription_status` attribute

### Lago
- Customer ID: Keycloak user ID
- Subscription: Plan code maps to tier
- Usage events: Track verifications for billing

### Ops-Center
- Existing tier system: VIP Founder, BYOK, Managed, etc.
- Can map to Data Intel tiers or keep separate

---

## Security Considerations

1. **API Keys**: Stored as bcrypt hashes, never logged
2. **RLS**: Database-level enforcement, not just application
3. **Rate Limiting**: Per-user, per-period, not bypassable
4. **Audit Trail**: All API calls logged with user context
5. **Key Rotation**: Support for regenerating keys without downtime

---

## Sources

- [Multi-tenant RLS Best Practices](https://www.thenile.dev/blog/multi-tenant-rls)
- [PostgreSQL RLS for SaaS](https://www.techbuddies.io/2026/01/01/how-to-implement-postgresql-row-level-security-for-multi-tenant-saas/)
- [RBAC for Multi-tenant SaaS 2025](https://workos.com/blog/top-rbac-providers-for-multi-tenant-saas-2025)
- [SaaS Architecture Patterns](https://medium.com/appfoster/architecture-patterns-for-saas-platforms-billing-rbac-and-onboarding-964ea071f571)
