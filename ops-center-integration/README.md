# Contact-Ops - Ops-Center Integration

This directory contains everything needed to integrate Contact-Ops into Ops-Center's admin panel.

## What You Get

After integration, Ops-Center admins can:

1. **View Stats Dashboard** - Companies, contacts, emails verified, by source app
2. **Manage API Keys** - Create, view, revoke API keys for apps
3. **View Recent Activity** - Last verifications, submissions
4. **Configure Settings** - SMTP timeout, concurrency, detection options
5. **Quick Verify** - Test email verification right from Ops-Center

## Files

| File | Purpose |
|------|---------|
| `DataIntelSettings.tsx` | React component for admin settings page |
| `integration.sql` | Database migrations for Ops-Center |
| `README.md` | This file |

## Installation

### Step 1: Run Database Migration

```bash
# Connect to Ops-Center's database and run the SQL
docker exec -i unicorn-postgresql psql -U unicorn -d unicorn_db < ops-center-integration/integration.sql
```

### Step 2: Add React Component

Copy the component to Ops-Center's frontend:

```bash
# Assuming Ops-Center source is at /home/muut/Production/Ops-Center-OSS
cp ops-center-integration/DataIntelSettings.tsx \
   /path/to/ops-center/frontend/src/pages/admin/settings/DataIntelSettings.tsx
```

### Step 3: Add Route

In Ops-Center's routes configuration (e.g., `routes.tsx` or `App.tsx`):

```tsx
import DataIntelSettings from './pages/admin/settings/DataIntelSettings';

// In your routes array:
{
  path: '/admin/settings/contact-ops',
  element: <DataIntelSettings />,
}
```

### Step 4: Add Menu Item

If Ops-Center uses a dynamic menu from the database, the SQL migration handles this.

If using hardcoded menu, add to the settings sidebar:

```tsx
{
  name: 'Contact-Ops',
  path: '/admin/settings/contact-ops',
  icon: <MailIcon />,
}
```

### Step 5: Configure Environment

Add to Ops-Center's `.env`:

```bash
VITE_CONTACT_OPS_API_URL=http://contact-ops-backend:8501
```

For production:
```bash
VITE_CONTACT_OPS_API_URL=https://verify.centerdeep.online
```

### Step 6: Rebuild Frontend

```bash
cd /path/to/ops-center/frontend
npm run build
docker compose restart ops-center-frontend
```

## API Endpoints Used

The component calls these Contact-Ops admin endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/admin/info` | GET | Service info and capabilities |
| `/api/v1/admin/stats` | GET | Dashboard statistics |
| `/api/v1/admin/stats/by-source` | GET | Stats by source app |
| `/api/v1/admin/activity` | GET | Activity time series |
| `/api/v1/admin/activity/recent` | GET | Recent activity log |
| `/api/v1/admin/api-keys` | GET | List API keys |
| `/api/v1/admin/api-keys` | POST | Create API key |
| `/api/v1/admin/api-keys/{id}` | DELETE | Revoke API key |
| `/api/v1/admin/settings` | GET/PUT | App settings |
| `/api/v1/verify/email` | POST | Quick verify |

## Screenshots

The integration provides:

```
Ops-Center Admin
└── Settings
    └── Contact-Ops
        ├── Overview Tab
        │   ├── Stats cards (companies, contacts, emails, etc.)
        │   ├── Source apps breakdown table
        │   └── Quick verify form
        ├── API Keys Tab
        │   ├── Create new key button
        │   ├── Keys list with revoke action
        │   └── Created key alert (shows once)
        ├── Settings Tab
        │   ├── SMTP timeout
        │   ├── SMTP concurrency
        │   ├── Max batch size
        │   ├── Detection toggles
        │   └── Save button
        └── Activity Tab
            └── Recent activity log
```

## Troubleshooting

### "Failed to fetch data" error

1. Check Contact-Ops is running: `curl http://localhost:8501/health`
2. Check network connectivity between Ops-Center and Contact-Ops
3. Verify `VITE_CONTACT_OPS_API_URL` is set correctly

### CORS errors

Contact-Ops allows all origins by default. For production, configure CORS in Contact-Ops's `.env`:

```bash
CORS_ORIGINS=https://ops.centerdeep.online
```

### API keys not showing

Run the integration SQL to create the initial API keys table and data.

## Network Configuration

Ensure both services are on the same Docker network:

```yaml
# In both docker-compose files
networks:
  - unicorn-network
```

## Security Notes

1. The admin endpoints don't require authentication in `STANDALONE_MODE`
2. For production, ensure OAuth2 Proxy is in front of both services
3. API keys are hashed in the database - full key shown only once on creation
