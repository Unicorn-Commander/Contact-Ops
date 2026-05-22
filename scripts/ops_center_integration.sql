-- Contact-Ops - Ops-Center Integration
-- Run this against the Ops-Center database (unicorn_db) to add Contact-Ops to the Apps Marketplace

-- Add Contact-Ops to the add_ons table
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
        "Full-text search"
    ]'::jsonb
) ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    launch_url = EXCLUDED.launch_url,
    features = EXCLUDED.features,
    is_active = EXCLUDED.is_active;

-- Enable for VIP Founder tier
INSERT INTO tier_features (tier_id, feature_key, enabled)
SELECT id, 'contact-ops', true
FROM subscription_tiers
WHERE tier_code = 'vip_founder'
ON CONFLICT DO NOTHING;

-- Enable for Founder Friend tier
INSERT INTO tier_features (tier_id, feature_key, enabled)
SELECT id, 'contact-ops', true
FROM subscription_tiers
WHERE tier_code = 'founder_friend'
ON CONFLICT DO NOTHING;

-- Enable for BYOK tier
INSERT INTO tier_features (tier_id, feature_key, enabled)
SELECT id, 'contact-ops', true
FROM subscription_tiers
WHERE tier_code IN ('byok', 'managed', 'professional', 'enterprise')
ON CONFLICT DO NOTHING;

-- Verify insertion
SELECT
    name,
    slug,
    category,
    is_active,
    jsonb_array_length(features) as feature_count
FROM add_ons
WHERE slug = 'contact-ops';

SELECT 'Contact-Ops added to Ops-Center Apps Marketplace!' as status;
