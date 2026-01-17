-- Migration 002: RBAC and Multi-tenant Support

-- 1. Add user tracking to api_keys table
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS source_apps TEXT[] DEFAULT '{}';
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS permissions TEXT[] DEFAULT '{}';

-- Create index for user lookups
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);

-- 2. Add owner tracking to catalogue tables
ALTER TABLE catalogue_companies ADD COLUMN IF NOT EXISTS owner_user_id UUID;
ALTER TABLE catalogue_contacts ADD COLUMN IF NOT EXISTS owner_user_id UUID;
ALTER TABLE catalogue_emails ADD COLUMN IF NOT EXISTS owner_user_id UUID;

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_companies_owner ON catalogue_companies(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_contacts_owner ON catalogue_contacts(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_emails_owner ON catalogue_emails(owner_user_id);
-- Note: source_apps is already a TEXT[] column, using GIN index for array queries
CREATE INDEX IF NOT EXISTS idx_companies_source_apps ON catalogue_companies USING GIN(source_apps);

-- 3. Create usage tracking table
CREATE TABLE IF NOT EXISTS usage_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    api_key_id UUID REFERENCES api_keys(id),
    action VARCHAR(50) NOT NULL,
    count INTEGER DEFAULT 1,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, action, period_start)
);

CREATE INDEX IF NOT EXISTS idx_usage_user_period ON usage_records(user_id, period_start, period_end);

-- 4. Create subscription_tiers reference table
CREATE TABLE IF NOT EXISTS dataintel_tiers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tier_code VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    price_monthly DECIMAL(10,2),
    verifications_per_period INTEGER,
    period_days INTEGER DEFAULT 30,
    permissions TEXT[] NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Insert default tiers
INSERT INTO dataintel_tiers (tier_code, name, description, price_monthly, verifications_per_period, period_days, permissions) VALUES
('trial', 'Trial', '7-day free trial', 0, 100, 7, ARRAY['verify:single']),
('basic', 'Basic', 'Email verification for small teams', 19.00, 1000, 30, ARRAY['verify:single', 'verify:batch', 'catalogue:submit', 'admin:keys']),
('pro', 'Pro', 'Full verification + data access', 49.00, 10000, 30, ARRAY['verify:single', 'verify:batch', 'catalogue:submit', 'catalogue:upload', 'catalogue:search:own', 'catalogue:lookup:own', 'admin:keys', 'admin:stats']),
('enterprise', 'Enterprise', 'Unlimited + cross-app access', 99.00, NULL, 30, ARRAY['verify:single', 'verify:batch', 'catalogue:submit', 'catalogue:upload', 'catalogue:search:own', 'catalogue:lookup:own', 'catalogue:search:cross', 'catalogue:lookup:cross', 'admin:keys', 'admin:stats'])
ON CONFLICT (tier_code) DO UPDATE SET
    permissions = EXCLUDED.permissions,
    verifications_per_period = EXCLUDED.verifications_per_period;
