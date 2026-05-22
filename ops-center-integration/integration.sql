-- Contact-Ops - Ops-Center Admin Integration
-- Run this against the Ops-Center database (unicorn_db)
--
-- This adds Contact-Ops to the Settings section with admin capabilities

-- ============================================================================
-- 1. Add to add_ons table (Apps Marketplace)
-- ============================================================================

INSERT INTO add_ons (
    name,
    slug,
    description,
    launch_url,
    category,
    feature_key,
    icon_url,
    is_active,
    features
) VALUES (
    'Contact-Ops',
    'contact-ops',
    'Email verification and enriched data cataloguing service. Verify emails via SMTP, catalogue company and contact data from all your apps, and build a unified intelligence database.',
    'https://verify.centerdeep.online',
    'tools',
    'contact-ops',
    '/logos/contact-ops-logo.svg',
    true,
    '[
        "SMTP email verification",
        "MX record validation",
        "Catch-all detection",
        "Disposable email blocking",
        "Risk scoring",
        "Data cataloguing",
        "Company normalization",
        "Contact deduplication",
        "CSV batch upload",
        "API for app integration",
        "Source tracking",
        "Full-text search",
        "Admin dashboard in Ops-Center"
    ]'::jsonb
) ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    launch_url = EXCLUDED.launch_url,
    features = EXCLUDED.features,
    is_active = EXCLUDED.is_active;

-- ============================================================================
-- 2. Add admin integration configuration
-- ============================================================================

-- Create integration_configs table if it doesn't exist
CREATE TABLE IF NOT EXISTS integration_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    api_url VARCHAR(500),
    api_key VARCHAR(500),
    is_enabled BOOLEAN DEFAULT false,
    config JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Add Contact-Ops integration config
INSERT INTO integration_configs (
    slug,
    name,
    description,
    api_url,
    is_enabled,
    config
) VALUES (
    'contact-ops',
    'Contact-Ops',
    'Email verification and data cataloguing service',
    'http://contact-ops-backend:8501',
    true,
    '{
        "admin_endpoints": {
            "stats": "/api/v1/admin/stats",
            "stats_by_source": "/api/v1/admin/stats/by-source",
            "activity": "/api/v1/admin/activity",
            "api_keys": "/api/v1/admin/api-keys",
            "settings": "/api/v1/admin/settings"
        },
        "verify_endpoints": {
            "single": "/api/v1/verify/email",
            "batch": "/api/v1/verify/batch"
        },
        "settings_page": "/admin/settings/contact-ops"
    }'::jsonb
) ON CONFLICT (slug) DO UPDATE SET
    api_url = EXCLUDED.api_url,
    config = EXCLUDED.config,
    updated_at = NOW();

-- ============================================================================
-- 3. Enable for subscription tiers
-- ============================================================================

-- Enable for VIP Founder tier (tier_features)
INSERT INTO tier_features (tier_id, feature_key, enabled)
SELECT id, 'contact-ops', true
FROM subscription_tiers
WHERE tier_code = 'vip_founder'
ON CONFLICT DO NOTHING;

-- Enable for VIP Founder tier (tier_apps - controls dashboard visibility)
INSERT INTO tier_apps (tier_id, app_key, enabled)
SELECT id, 'contact-ops', true
FROM subscription_tiers
WHERE tier_code = 'vip_founder'
ON CONFLICT (tier_id, app_key) DO UPDATE SET enabled = true;

-- Enable for Founder Friend tier
INSERT INTO tier_features (tier_id, feature_key, enabled)
SELECT id, 'contact-ops', true
FROM subscription_tiers
WHERE tier_code = 'founder_friend'
ON CONFLICT DO NOTHING;

-- Enable for BYOK and higher tiers (tier_features)
INSERT INTO tier_features (tier_id, feature_key, enabled)
SELECT id, 'contact-ops', true
FROM subscription_tiers
WHERE tier_code IN ('byok', 'managed', 'professional', 'enterprise')
ON CONFLICT DO NOTHING;

-- Enable for BYOK and higher tiers (tier_apps)
INSERT INTO tier_apps (tier_id, app_key, enabled)
SELECT id, 'contact-ops', true
FROM subscription_tiers
WHERE tier_code IN ('byok', 'managed', 'professional', 'enterprise')
ON CONFLICT (tier_id, app_key) DO UPDATE SET enabled = true;

-- ============================================================================
-- 4. Add settings menu item (if using dynamic menu system)
-- ============================================================================

-- Create settings_menu table if it doesn't exist
CREATE TABLE IF NOT EXISTS settings_menu (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    icon VARCHAR(50),
    path VARCHAR(200) NOT NULL,
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    requires_feature VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Add Contact-Ops to settings menu
INSERT INTO settings_menu (
    slug,
    name,
    icon,
    path,
    sort_order,
    is_active,
    requires_feature
) VALUES (
    'contact-ops',
    'Contact-Ops',
    'Mail',
    '/admin/settings/contact-ops',
    50,
    true,
    'contact-ops'
) ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    path = EXCLUDED.path,
    is_active = EXCLUDED.is_active;

-- ============================================================================
-- 5. Verify installation
-- ============================================================================

SELECT 'Contact-Ops integration added!' as status;

SELECT
    name,
    slug,
    is_active,
    jsonb_array_length(features) as feature_count
FROM add_ons
WHERE slug = 'contact-ops';

SELECT
    slug,
    name,
    is_enabled,
    api_url
FROM integration_configs
WHERE slug = 'contact-ops';
