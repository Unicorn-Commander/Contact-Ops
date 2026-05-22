"""persons and organizations

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # persons — extends catalogue_contacts
    op.execute("""
        CREATE TABLE persons (
            id                          uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            legacy_contact_id           uuid UNIQUE,
            kind                        person_kind NOT NULL DEFAULT 'individual',
            display_name                text NOT NULL,
            family_name                 text,
            given_name                  text,
            additional_names            text[] NOT NULL DEFAULT '{}',
            honorific_prefix            text,
            honorific_suffix            text,
            phonetic_family_name        text,
            phonetic_given_name         text,
            nicknames                   text[] NOT NULL DEFAULT '{}',
            file_as                     text,
            pronouns                    text,
            gender_identity             text,
            birthday                    jsonb,
            anniversary                 jsonb,
            death_date                  jsonb,
            is_deceased                 boolean NOT NULL DEFAULT false,
            preferred_languages         text[] NOT NULL DEFAULT '{}',
            time_zone                   text,
            preferred_contact_channel   preferred_contact NOT NULL DEFAULT 'unknown',
            preferred_contact_time_window jsonb,
            headline                    text,
            bio                         text,
            occupation_title            text,
            current_org_id              uuid,
            current_org_role_id         uuid,
            canonical_owner_tenant_id   uuid NOT NULL REFERENCES tenants(id),
            is_personal                 boolean NOT NULL DEFAULT false,
            vcard_uid                   text UNIQUE,
            source_pid_map              jsonb NOT NULL DEFAULT '{}',
            merge_status                merge_status NOT NULL DEFAULT 'canonical',
            merged_into_id              uuid REFERENCES persons(id),
            name_embedding              vector(384),
            bio_embedding               vector(1024),
            voice_fingerprint_id        uuid,
            face_embedding              vector(512),
            communication_strength_score real NOT NULL DEFAULT 0,
            last_interaction_at         timestamptz,
            last_inbound_at             timestamptz,
            last_outbound_at            timestamptz,
            interaction_count_30d       integer NOT NULL DEFAULT 0,
            interaction_count_90d       integer NOT NULL DEFAULT 0,
            interaction_count_365d      integer NOT NULL DEFAULT 0,
            preferred_channel_inferred  contact_channel,
            response_latency_p50_hours  real,
            relationship_temperature    temperature_state NOT NULL DEFAULT 'cold',
            last_topic_clusters         jsonb,
            inferred_attributes         jsonb NOT NULL DEFAULT '{}',
            retention_class             retention_class NOT NULL DEFAULT 'operational_2y',
            legal_hold                  boolean NOT NULL DEFAULT false,
            created_at                  timestamptz NOT NULL DEFAULT now(),
            updated_at                  timestamptz NOT NULL DEFAULT now(),
            created_by_actor_id         uuid,
            etag                        text NOT NULL DEFAULT '',
            CONSTRAINT persons_merge_chk CHECK (
                (merge_status = 'merged_into') = (merged_into_id IS NOT NULL)
            )
        )
    """)
    op.execute("CREATE INDEX persons_owner_idx ON persons(canonical_owner_tenant_id)")
    op.execute("CREATE INDEX persons_display_trgm_idx ON persons USING gin (display_name gin_trgm_ops)")
    op.execute("CREATE INDEX persons_family_trgm_idx ON persons USING gin (family_name gin_trgm_ops)")
    op.execute("CREATE INDEX persons_nicknames_gin_idx ON persons USING gin (nicknames)")
    op.execute("CREATE INDEX persons_inferred_gin_idx ON persons USING gin (inferred_attributes jsonb_path_ops)")
    op.execute("CREATE INDEX persons_last_interaction_idx ON persons(last_interaction_at DESC NULLS LAST)")
    op.execute("CREATE INDEX persons_temperature_idx ON persons(canonical_owner_tenant_id, relationship_temperature)")
    op.execute("CREATE INDEX persons_canonical_only_idx ON persons(id) WHERE merge_status = 'canonical'")
    op.execute("CREATE INDEX persons_legacy_idx ON persons(legacy_contact_id) WHERE legacy_contact_id IS NOT NULL")
    op.execute("CREATE INDEX persons_name_embed_hnsw ON persons USING hnsw (name_embedding vector_cosine_ops)")
    op.execute("CREATE INDEX persons_bio_embed_hnsw ON persons USING hnsw (bio_embedding vector_cosine_ops)")
    op.execute("CREATE INDEX persons_face_embed_hnsw ON persons USING hnsw (face_embedding vector_l2_ops)")
    op.execute("CREATE INDEX persons_vcard_uid_idx ON persons(vcard_uid) WHERE vcard_uid IS NOT NULL")
    op.execute("COMMENT ON COLUMN persons.canonical_owner_tenant_id IS 'Tenant that owns this canonical row. Other tenants reference via person_tenant_membership.'")
    op.execute("COMMENT ON COLUMN persons.communication_strength_score IS 'Materialized 0..1 score recomputed nightly by Communication Signal Recomputer agent.'")

    # organizations — extends catalogue_companies
    op.execute("""
        CREATE TABLE organizations (
            id                       uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            legacy_company_id        uuid UNIQUE,
            kind                     org_kind NOT NULL DEFAULT 'company',
            legal_name               text NOT NULL,
            display_name             text NOT NULL,
            dba_names                text[] NOT NULL DEFAULT '{}',
            description              text,
            industry                 text,
            naics_codes              text[] NOT NULL DEFAULT '{}',
            sic_codes                text[] NOT NULL DEFAULT '{}',
            employee_count_estimate  integer,
            employee_count_range     text,
            annual_revenue_estimate  numeric(18,2),
            annual_revenue_range     text,
            founded_year             integer CHECK (founded_year BETWEEN 1500 AND 2999),
            dissolved_year           integer CHECK (dissolved_year IS NULL OR dissolved_year BETWEEN 1500 AND 2999),
            ticker_symbol            text,
            cik                      text,
            duns_number              text,
            sam_uei                  text,
            ein_enc                  bytea,
            cage_code                text,
            domain                   text,
            linkedin_company_id      text,
            crunchbase_uuid          uuid,
            logo_asset_id            uuid,
            parent_org_id            uuid REFERENCES organizations(id),
            headquarters_address_id  uuid,
            tax_status               text,
            state_of_incorporation   text,
            is_sdvosb                boolean NOT NULL DEFAULT false,
            is_minority_owned        boolean NOT NULL DEFAULT false,
            is_woman_owned           boolean NOT NULL DEFAULT false,
            description_embedding    vector(1024),
            canonical_owner_tenant_id uuid NOT NULL REFERENCES tenants(id),
            merge_status              merge_status NOT NULL DEFAULT 'canonical',
            merged_into_id            uuid REFERENCES organizations(id),
            retention_class          retention_class NOT NULL DEFAULT 'operational_2y',
            legal_hold               boolean NOT NULL DEFAULT false,
            created_at               timestamptz NOT NULL DEFAULT now(),
            updated_at               timestamptz NOT NULL DEFAULT now(),
            created_by_actor_id      uuid,
            etag                     text NOT NULL DEFAULT '',
            CONSTRAINT orgs_merge_chk CHECK (
                (merge_status = 'merged_into') = (merged_into_id IS NOT NULL)
            )
        )
    """)
    op.execute("CREATE INDEX organizations_owner_idx ON organizations(canonical_owner_tenant_id)")
    op.execute("CREATE INDEX organizations_legal_trgm_idx ON organizations USING gin (legal_name gin_trgm_ops)")
    op.execute("CREATE INDEX organizations_display_trgm_idx ON organizations USING gin (display_name gin_trgm_ops)")
    op.execute("CREATE INDEX organizations_domain_idx ON organizations(lower(domain)) WHERE domain IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX organizations_domain_uniq ON organizations(canonical_owner_tenant_id, lower(domain)) WHERE domain IS NOT NULL")
    op.execute("CREATE INDEX organizations_industry_idx ON organizations(industry)")
    op.execute("CREATE INDEX organizations_desc_embed_hnsw ON organizations USING hnsw (description_embedding vector_cosine_ops)")

    # FK: persons.current_org_id → organizations.id
    op.execute("""
        ALTER TABLE persons ADD CONSTRAINT persons_current_org_fk
            FOREIGN KEY (current_org_id) REFERENCES organizations(id)
    """)

    # Backward-compat view
    op.execute("""
        CREATE VIEW catalogue_contacts AS
        SELECT id, legacy_contact_id, kind, display_name, family_name, given_name,
               additional_names, honorific_prefix, honorific_suffix,
               phonetic_family_name, phonetic_given_name, nicknames, file_as,
               pronouns, gender_identity, birthday, anniversary, death_date,
               is_deceased, preferred_languages, time_zone,
               preferred_contact_channel, preferred_contact_time_window,
               headline, bio, occupation_title, current_org_id, current_org_role_id,
               canonical_owner_tenant_id AS owner_tenant_id, is_personal,
               vcard_uid, source_pid_map, merge_status, merged_into_id,
               name_embedding, bio_embedding, voice_fingerprint_id, face_embedding,
               communication_strength_score, last_interaction_at, last_inbound_at,
               last_outbound_at, interaction_count_30d, interaction_count_90d,
               interaction_count_365d, preferred_channel_inferred,
               response_latency_p50_hours, relationship_temperature,
               last_topic_clusters, inferred_attributes, retention_class,
               legal_hold, created_at, updated_at, created_by_actor_id, etag
        FROM persons
        WHERE merge_status = 'canonical'
    """)

    op.execute("""
        CREATE VIEW catalogue_companies AS
        SELECT id, legacy_company_id, kind, legal_name, display_name, dba_names,
               description, industry, naics_codes, sic_codes,
               employee_count_estimate, employee_count_range,
               annual_revenue_estimate, annual_revenue_range,
               founded_year, dissolved_year, ticker_symbol, cik, duns_number,
               sam_uei, ein_enc, cage_code, domain, linkedin_company_id,
               crunchbase_uuid, logo_asset_id, parent_org_id,
               headquarters_address_id, tax_status, state_of_incorporation,
               is_sdvosb, is_minority_owned, is_woman_owned,
               description_embedding, canonical_owner_tenant_id,
               merge_status, merged_into_id, retention_class, legal_hold,
               created_at, updated_at, created_by_actor_id, etag
        FROM organizations
        WHERE merge_status = 'canonical'
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS catalogue_companies")
    op.execute("DROP VIEW IF EXISTS catalogue_contacts")
    op.execute("ALTER TABLE persons DROP CONSTRAINT IF EXISTS persons_current_org_fk")
    op.execute("DROP TABLE IF EXISTS organizations")
    op.execute("DROP TABLE IF EXISTS persons")
