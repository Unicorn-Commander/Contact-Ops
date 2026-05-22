"""tenant membership, multi-cardinality contact attributes

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # person_tenant_membership
    op.execute("""
        CREATE TABLE person_tenant_membership (
            person_id    uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            visibility   text NOT NULL DEFAULT 'visible' CHECK (visibility IN ('visible','hidden','archived')),
            notes        text,
            tags         text[] NOT NULL DEFAULT '{}',
            custom_attrs jsonb NOT NULL DEFAULT '{}',
            consent_basis consent_basis,
            added_at     timestamptz NOT NULL DEFAULT now(),
            added_by     uuid,
            last_accessed_at timestamptz,
            PRIMARY KEY (person_id, tenant_id)
        )
    """)
    op.execute("CREATE INDEX ptm_tenant_idx ON person_tenant_membership(tenant_id, visibility)")
    op.execute("CREATE INDEX ptm_tags_gin_idx ON person_tenant_membership USING gin (tags)")
    op.execute("CREATE INDEX ptm_attrs_gin_idx ON person_tenant_membership USING gin (custom_attrs jsonb_path_ops)")

    # organization_tenant_membership
    op.execute("""
        CREATE TABLE organization_tenant_membership (
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            tenant_id       uuid NOT NULL REFERENCES tenants(id)       ON DELETE CASCADE,
            visibility      text NOT NULL DEFAULT 'visible' CHECK (visibility IN ('visible','hidden','archived')),
            notes           text,
            tags            text[] NOT NULL DEFAULT '{}',
            custom_attrs    jsonb NOT NULL DEFAULT '{}',
            added_at        timestamptz NOT NULL DEFAULT now(),
            added_by        uuid,
            last_accessed_at timestamptz,
            PRIMARY KEY (organization_id, tenant_id)
        )
    """)
    op.execute("CREATE INDEX otm_tenant_idx ON organization_tenant_membership(tenant_id, visibility)")
    op.execute("CREATE INDEX otm_tags_gin ON organization_tenant_membership USING gin (tags)")

    # emails — extends catalogue_emails
    op.execute("""
        CREATE TABLE emails (
            id                  uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id           uuid REFERENCES persons(id) ON DELETE CASCADE,
            organization_id     uuid REFERENCES organizations(id) ON DELETE CASCADE,
            address             citext NOT NULL,
            type                email_type NOT NULL DEFAULT 'other',
            label               text,
            is_primary          boolean NOT NULL DEFAULT false,
            is_verified         boolean NOT NULL DEFAULT false,
            verified_at         timestamptz,
            deliverability_status email_deliverability NOT NULL DEFAULT 'unknown',
            last_bounced_at     timestamptz,
            bounce_reason       text,
            opted_out           boolean NOT NULL DEFAULT false,
            source_id           uuid,
            confidence          numeric(4,3) NOT NULL DEFAULT 1.000,
            observed_at         timestamptz NOT NULL DEFAULT now(),
            valid_from          timestamptz,
            valid_until         timestamptz,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT email_owner_xor CHECK (
                (person_id IS NOT NULL)::int + (organization_id IS NOT NULL)::int = 1
            ),
            CONSTRAINT email_window_ok CHECK (valid_window_ok(valid_from, valid_until))
        )
    """)
    op.execute("CREATE UNIQUE INDEX emails_person_lower_uniq ON emails(person_id, lower(address)) WHERE person_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX emails_org_lower_uniq ON emails(organization_id, lower(address)) WHERE organization_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX emails_person_primary_uniq ON emails(person_id) WHERE is_primary AND person_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX emails_org_primary_uniq ON emails(organization_id) WHERE is_primary AND organization_id IS NOT NULL")
    op.execute("CREATE INDEX emails_address_idx ON emails(lower(address))")
    op.execute("CREATE INDEX emails_address_trgm_idx ON emails USING gin (address gin_trgm_ops)")
    op.execute("CREATE INDEX emails_optout_idx ON emails(person_id) WHERE opted_out")

    # phones — extends existing phone fields from catalogue_contacts
    op.execute("""
        CREATE TABLE phones (
            id                uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id         uuid REFERENCES persons(id) ON DELETE CASCADE,
            organization_id   uuid REFERENCES organizations(id) ON DELETE CASCADE,
            e164              text NOT NULL CHECK (e164 ~ '^[+][1-9][0-9]{6,15}$'),
            extension         text,
            type              phone_type NOT NULL DEFAULT 'other',
            label             text,
            is_primary        boolean NOT NULL DEFAULT false,
            is_sms_capable    boolean NOT NULL DEFAULT false,
            is_whatsapp       boolean NOT NULL DEFAULT false,
            is_signal         boolean NOT NULL DEFAULT false,
            is_imessage       boolean NOT NULL DEFAULT false,
            opted_out_sms     boolean NOT NULL DEFAULT false,
            do_not_call       boolean NOT NULL DEFAULT false,
            carrier           text,
            line_type         line_type NOT NULL DEFAULT 'unknown',
            country_code      smallint,
            national_number   text,
            source_id         uuid,
            confidence        numeric(4,3) NOT NULL DEFAULT 1.000,
            observed_at       timestamptz NOT NULL DEFAULT now(),
            valid_from        timestamptz,
            valid_until       timestamptz,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT phone_owner_xor CHECK (
                (person_id IS NOT NULL)::int + (organization_id IS NOT NULL)::int = 1
            )
        )
    """)
    op.execute("CREATE UNIQUE INDEX phones_person_e164_uniq ON phones(person_id, e164) WHERE person_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX phones_org_e164_uniq ON phones(organization_id, e164) WHERE organization_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX phones_person_primary_uniq ON phones(person_id) WHERE is_primary AND person_id IS NOT NULL")
    op.execute("CREATE INDEX phones_e164_idx ON phones(e164)")
    op.execute("CREATE INDEX phones_dnc_idx ON phones(person_id) WHERE do_not_call OR opted_out_sms")

    # postal_addresses
    op.execute("""
        CREATE TABLE postal_addresses (
            id                  uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id           uuid REFERENCES persons(id) ON DELETE CASCADE,
            organization_id     uuid REFERENCES organizations(id) ON DELETE CASCADE,
            formatted           text,
            po_box              text,
            street_address      text,
            extended_address    text,
            locality            text,
            region              text,
            region_code         text,
            postal_code         text,
            country_name        text,
            country_code        char(2),
            geo_lat             numeric(9,6),
            geo_lng             numeric(9,6),
            geo_precision       geo_precision NOT NULL DEFAULT 'unknown',
            type                address_type NOT NULL DEFAULT 'other',
            label               text,
            is_primary          boolean NOT NULL DEFAULT false,
            verified_via        address_verified_via NOT NULL DEFAULT 'unverified',
            verified_at         timestamptz,
            source_id           uuid,
            confidence          numeric(4,3) NOT NULL DEFAULT 1.000,
            observed_at         timestamptz NOT NULL DEFAULT now(),
            valid_from          timestamptz,
            valid_until         timestamptz,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT addr_owner_xor CHECK (
                (person_id IS NOT NULL)::int + (organization_id IS NOT NULL)::int = 1
            )
        )
    """)
    op.execute("CREATE UNIQUE INDEX addr_person_primary_uniq ON postal_addresses(person_id) WHERE is_primary AND person_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX addr_org_primary_uniq ON postal_addresses(organization_id) WHERE is_primary AND organization_id IS NOT NULL")
    op.execute("CREATE INDEX addr_postal_idx ON postal_addresses(postal_code)")
    op.execute("CREATE INDEX addr_country_locality_idx ON postal_addresses(country_code, region_code, locality)")
    op.execute("CREATE INDEX addr_geo_idx ON postal_addresses USING gist (point(geo_lng, geo_lat)) WHERE geo_lat IS NOT NULL")
    op.execute("""
        ALTER TABLE organizations ADD CONSTRAINT orgs_hq_fk
            FOREIGN KEY (headquarters_address_id) REFERENCES postal_addresses(id)
    """)

    # identifiers
    op.execute("""
        CREATE TABLE identifiers (
            id            uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id     uuid REFERENCES persons(id) ON DELETE CASCADE,
            organization_id uuid REFERENCES organizations(id) ON DELETE CASCADE,
            namespace     text NOT NULL,
            value         text NOT NULL,
            url           text,
            verified      boolean NOT NULL DEFAULT false,
            observed_at   timestamptz NOT NULL DEFAULT now(),
            source_id     uuid,
            confidence    numeric(4,3) NOT NULL DEFAULT 1.000,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT identifier_owner_xor CHECK (
                (person_id IS NOT NULL)::int + (organization_id IS NOT NULL)::int = 1
            )
        )
    """)
    op.execute("CREATE UNIQUE INDEX identifiers_person_ns_val_uniq ON identifiers(person_id, namespace, lower(value)) WHERE person_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX identifiers_org_ns_val_uniq ON identifiers(organization_id, namespace, lower(value)) WHERE organization_id IS NOT NULL")
    op.execute("CREATE INDEX identifiers_ns_val_lookup_idx ON identifiers(namespace, lower(value))")
    op.execute("COMMENT ON TABLE identifiers IS 'Stable third-party identifiers. Critical for dedup.'")

    # im_handles
    op.execute("""
        CREATE TABLE im_handles (
            id         uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id  uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            protocol   text NOT NULL,
            handle     text NOT NULL,
            label      text,
            is_primary boolean NOT NULL DEFAULT false,
            source_id  uuid,
            confidence numeric(4,3) NOT NULL DEFAULT 1.000,
            observed_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE UNIQUE INDEX im_handles_uniq ON im_handles(person_id, protocol, lower(handle))")
    op.execute("CREATE INDEX im_handles_lookup_idx ON im_handles(protocol, lower(handle))")

    # urls
    op.execute("""
        CREATE TABLE urls (
            id              uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id       uuid REFERENCES persons(id) ON DELETE CASCADE,
            organization_id uuid REFERENCES organizations(id) ON DELETE CASCADE,
            url             text NOT NULL,
            type            text NOT NULL DEFAULT 'profile',
            label           text,
            is_primary      boolean NOT NULL DEFAULT false,
            source_id       uuid,
            confidence      numeric(4,3) NOT NULL DEFAULT 1.000,
            observed_at     timestamptz NOT NULL DEFAULT now(),
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT urls_owner_xor CHECK (
                (person_id IS NOT NULL)::int + (organization_id IS NOT NULL)::int = 1
            )
        )
    """)
    op.execute("CREATE INDEX urls_person_idx ON urls(person_id)")
    op.execute("CREATE INDEX urls_org_idx ON urls(organization_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE organizations DROP CONSTRAINT IF EXISTS orgs_hq_fk")
    op.execute("DROP TABLE IF EXISTS urls")
    op.execute("DROP TABLE IF EXISTS im_handles")
    op.execute("DROP TABLE IF EXISTS identifiers")
    op.execute("DROP TABLE IF EXISTS postal_addresses")
    op.execute("DROP TABLE IF EXISTS phones")
    op.execute("DROP TABLE IF EXISTS emails")
    op.execute("DROP TABLE IF EXISTS organization_tenant_membership")
    op.execute("DROP TABLE IF EXISTS person_tenant_membership")
