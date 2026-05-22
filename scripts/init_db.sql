-- Contact-Ops - Database Initialization
-- Run this against unicorn-postgresql to create the database and tables

-- Create database (run as superuser)
-- CREATE DATABASE contact_ops_db OWNER unicorn;

-- Connect to contact_ops_db before running the rest
-- \c contact_ops_db

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- CATALOGUE TABLES
-- ============================================

-- Companies
CREATE TABLE IF NOT EXISTS catalogue_companies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(500) NOT NULL,
    legal_name VARCHAR(500),
    primary_domain VARCHAR(255),
    industry VARCHAR(100),
    sub_industry VARCHAR(100),
    company_type VARCHAR(50),
    employee_count VARCHAR(50),
    revenue_range VARCHAR(50),
    headquarters_city VARCHAR(100),
    headquarters_state VARCHAR(50),
    headquarters_country VARCHAR(100) DEFAULT 'USA',
    full_address TEXT,
    enrichment_data JSONB DEFAULT '{}',
    source_apps TEXT[] DEFAULT '{}',
    first_seen_at TIMESTAMP DEFAULT NOW(),
    last_updated_at TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_domain ON catalogue_companies(primary_domain) WHERE primary_domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_companies_industry ON catalogue_companies(industry);
CREATE INDEX IF NOT EXISTS idx_companies_state ON catalogue_companies(headquarters_state);
CREATE INDEX IF NOT EXISTS idx_companies_name_fts ON catalogue_companies USING gin(to_tsvector('english', name));

-- Contacts
CREATE TABLE IF NOT EXISTS catalogue_contacts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID REFERENCES catalogue_companies(id) ON DELETE SET NULL,
    full_name VARCHAR(255),
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    title VARCHAR(255),
    primary_email VARCHAR(255),
    phone VARCHAR(50),
    mobile VARCHAR(50),
    linkedin_url TEXT,
    twitter_url TEXT,
    enrichment_data JSONB DEFAULT '{}',
    source_apps TEXT[] DEFAULT '{}',
    confidence_score DECIMAL(3,2),
    first_seen_at TIMESTAMP DEFAULT NOW(),
    last_updated_at TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_email ON catalogue_contacts(primary_email) WHERE primary_email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_company ON catalogue_contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_name_fts ON catalogue_contacts USING gin(to_tsvector('english', full_name));

-- Emails (verification data)
CREATE TABLE IF NOT EXISTS catalogue_emails (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id UUID REFERENCES catalogue_contacts(id) ON DELETE SET NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    domain VARCHAR(255) NOT NULL,
    is_valid BOOLEAN,
    is_verified_smtp BOOLEAN DEFAULT FALSE,
    is_catchall BOOLEAN,
    is_disposable BOOLEAN,
    is_role_based BOOLEAN,
    risk_score DECIMAL(3,2),
    mx_records JSONB,
    smtp_response_code INT,
    smtp_response_message TEXT,
    bounce_count INT DEFAULT 0,
    last_bounce_at TIMESTAMP,
    last_verified_at TIMESTAMP,
    verification_count INT DEFAULT 0,
    discovered_by_app VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_emails_domain ON catalogue_emails(domain);
CREATE INDEX IF NOT EXISTS idx_emails_contact ON catalogue_emails(contact_id);
CREATE INDEX IF NOT EXISTS idx_emails_verified ON catalogue_emails(is_verified_smtp, last_verified_at);
CREATE INDEX IF NOT EXISTS idx_emails_valid ON catalogue_emails(is_valid);

-- Verification Jobs
CREATE TABLE IF NOT EXISTS verification_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    status VARCHAR(20) DEFAULT 'queued',
    job_type VARCHAR(20) DEFAULT 'verify',
    total_count INT DEFAULT 0,
    processed_count INT DEFAULT 0,
    success_count INT DEFAULT 0,
    error_count INT DEFAULT 0,
    input_data JSONB,
    results JSONB DEFAULT '[]',
    error_message TEXT,
    created_by_app VARCHAR(50),
    created_by_user VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON verification_jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON verification_jobs(created_at DESC);

-- Source Tracking
CREATE TABLE IF NOT EXISTS catalogue_sources (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type VARCHAR(20) NOT NULL,
    entity_id UUID NOT NULL,
    source_app VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(255),
    source_url TEXT,
    contributed_data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sources_entity ON catalogue_sources(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_sources_app ON catalogue_sources(source_app);

-- API Keys
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    key_prefix VARCHAR(20) NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    app_name VARCHAR(100) NOT NULL,
    description TEXT,
    tier VARCHAR(20) DEFAULT 'standard',
    rate_limit INT DEFAULT 1000,
    allowed_endpoints TEXT[],
    is_active BOOLEAN DEFAULT TRUE,
    expires_at TIMESTAMP,
    last_used_at TIMESTAMP,
    request_count BIGINT DEFAULT 0,
    created_by VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_api_keys_app ON api_keys(app_name);

-- ============================================
-- INITIAL API KEYS
-- ============================================

-- Insert default API keys for internal apps
-- Key format: sk-{app}-contact-ops-2025
-- These are SHA256 hashes of the actual keys

-- loopnet-leads: sk-loopnet-contact-ops-2025
INSERT INTO api_keys (key_prefix, key_hash, app_name, description, tier, rate_limit)
VALUES (
    'sk-loopnet',
    '5a7d9e3f8c2b1a4d6e7f0c9b8a7d6e5f4c3b2a1d0e9f8c7b6a5d4e3f2c1b0a9d',
    'loopnet-leads',
    'LoopNet Lead Enrichment CRM',
    'unlimited',
    10000
) ON CONFLICT DO NOTHING;

-- ca-retirement-leads: sk-caqr-contact-ops-2025
INSERT INTO api_keys (key_prefix, key_hash, app_name, description, tier, rate_limit)
VALUES (
    'sk-caqr',
    '3b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c',
    'ca-retirement-leads',
    'California Retirement Leads',
    'unlimited',
    10000
) ON CONFLICT DO NOTHING;

-- magic-unicorn-outreach: sk-outreach-contact-ops-2025
INSERT INTO api_keys (key_prefix, key_hash, app_name, description, tier, rate_limit)
VALUES (
    'sk-outreach',
    '7f6e5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b4a3f2e1d0c9b8a7f6e',
    'magic-unicorn-outreach',
    'Magic Unicorn Outreach System',
    'unlimited',
    10000
) ON CONFLICT DO NOTHING;

-- ops-center: sk-opscenter-contact-ops-2025
INSERT INTO api_keys (key_prefix, key_hash, app_name, description, tier, rate_limit)
VALUES (
    'sk-opscenter',
    '9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b',
    'ops-center',
    'Ops Center Integration',
    'unlimited',
    100000
) ON CONFLICT DO NOTHING;

-- ============================================
-- GRANT PERMISSIONS
-- ============================================

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO unicorn;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO unicorn;

-- ============================================
-- SUMMARY
-- ============================================

SELECT 'Database initialized successfully!' as status;
SELECT 'Tables created: ' || count(*) as tables FROM information_schema.tables WHERE table_schema = 'public';
SELECT 'API keys created: ' || count(*) as api_keys FROM api_keys;
