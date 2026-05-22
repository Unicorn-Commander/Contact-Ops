"""rls_policies_hipaa_triggers_etag

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Helper: read tenant_id from JWT-issued GUC
    op.execute("""
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$
    """)

    # Helper: HIPAA fence check
    op.execute("""
        CREATE OR REPLACE FUNCTION tenant_is_hipaa(t uuid) RETURNS boolean
        LANGUAGE sql STABLE
        AS $$ SELECT coalesce((SELECT hipaa_mode FROM tenants WHERE id = t), false) $$
    """)

    # Enable RLS on all data tables
    tables_to_rls = [
        'persons', 'organizations', 'emails', 'phones', 'postal_addresses',
        'identifiers', 'im_handles', 'urls', 'photos', 'voice_samples',
        'voice_fingerprints', 'person_org_role', 'person_person_relation',
        'org_org_relation', 'facts', 'field_provenance', 'interactions',
        'consent_records', 'action_event', 'merge_history',
        'person_tenant_membership', 'organization_tenant_membership',
    ]
    for tbl in tables_to_rls:
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")

    # Persons RLS policies
    op.execute("""
        CREATE POLICY persons_select ON persons FOR SELECT TO contact_ops_app
            USING (
                canonical_owner_tenant_id = current_tenant_id()
                OR EXISTS (
                    SELECT 1 FROM person_tenant_membership m
                    WHERE m.person_id = persons.id
                      AND m.tenant_id = current_tenant_id()
                      AND m.visibility <> 'archived'
                )
            )
    """)
    op.execute("""
        CREATE POLICY persons_modify ON persons FOR UPDATE TO contact_ops_app
            USING (canonical_owner_tenant_id = current_tenant_id())
            WITH CHECK (canonical_owner_tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY persons_insert ON persons FOR INSERT TO contact_ops_app
            WITH CHECK (canonical_owner_tenant_id = current_tenant_id())
    """)

    # Organizations RLS policies (same pattern)
    op.execute("""
        CREATE POLICY organizations_select ON organizations FOR SELECT TO contact_ops_app
            USING (canonical_owner_tenant_id = current_tenant_id()
                OR EXISTS (
                    SELECT 1 FROM organization_tenant_membership m
                    WHERE m.organization_id = organizations.id
                      AND m.tenant_id = current_tenant_id()
                      AND m.visibility <> 'archived'
                )
            )
    """)
    op.execute("""
        CREATE POLICY organizations_modify ON organizations FOR UPDATE TO contact_ops_app
            USING (canonical_owner_tenant_id = current_tenant_id())
            WITH CHECK (canonical_owner_tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY organizations_insert ON organizations FOR INSERT TO contact_ops_app
            WITH CHECK (canonical_owner_tenant_id = current_tenant_id())
    """)

    # Emails RLS
    op.execute("""
        CREATE POLICY emails_select ON emails FOR SELECT TO contact_ops_app
            USING (
                (person_id IS NOT NULL AND canonical_owner_tenant_id = current_tenant_id())
                OR (organization_id IS NOT NULL AND canonical_owner_tenant_id = current_tenant_id())
            )
    """)
    op.execute("""
        CREATE POLICY emails_modify ON emails FOR ALL TO contact_ops_app
            USING (
                (person_id IS NOT NULL AND canonical_owner_tenant_id = current_tenant_id())
                OR (organization_id IS NOT NULL AND canonical_owner_tenant_id = current_tenant_id())
            )
            WITH CHECK (
                (person_id IS NOT NULL AND canonical_owner_tenant_id = current_tenant_id())
                OR (organization_id IS NOT NULL AND canonical_owner_tenant_id = current_tenant_id())
            )
    """)
    # Fix: emails needs canonical_owner_tenant_id from related persons/orgs
    # Simpler policy: tenant_id from person_tenant_membership or organization_tenant_membership
    op.execute("DROP POLICY IF EXISTS emails_select ON emails")
    op.execute("DROP POLICY IF EXISTS emails_modify ON emails")
    op.execute("""
        CREATE POLICY emails_select ON emails FOR SELECT TO contact_ops_app
            USING (
                EXISTS (
                    SELECT 1 FROM persons p
                    WHERE p.id = emails.person_id
                      AND (p.canonical_owner_tenant_id = current_tenant_id()
                           OR EXISTS (
                               SELECT 1 FROM person_tenant_membership m
                               WHERE m.person_id = p.id
                                 AND m.tenant_id = current_tenant_id()
                               LIMIT 1
                           ))
                )
                OR EXISTS (
                    SELECT 1 FROM organizations o
                    WHERE o.id = emails.organization_id
                      AND (o.canonical_owner_tenant_id = current_tenant_id()
                           OR EXISTS (
                               SELECT 1 FROM organization_tenant_membership m
                               WHERE m.organization_id = o.id
                                 AND m.tenant_id = current_tenant_id()
                               LIMIT 1
                           ))
                )
            )
    """)
    op.execute("""
        CREATE POLICY emails_modify ON emails FOR ALL TO contact_ops_app
            USING (
                EXISTS (
                    SELECT 1 FROM persons p
                    WHERE p.id = emails.person_id
                      AND p.canonical_owner_tenant_id = current_tenant_id()
                )
                OR EXISTS (
                    SELECT 1 FROM organizations o
                    WHERE o.id = emails.organization_id
                      AND o.canonical_owner_tenant_id = current_tenant_id()
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1 FROM persons p
                    WHERE p.id = emails.person_id
                      AND p.canonical_owner_tenant_id = current_tenant_id()
                )
                OR EXISTS (
                    SELECT 1 FROM organizations o
                    WHERE o.id = emails.organization_id
                      AND o.canonical_owner_tenant_id = current_tenant_id()
                )
            )
    """)

    # Generic RLS for remaining tables (tenant_id column pattern)
    tenant_tables = [
        ('phones', 'phone'), ('postal_addresses', 'addr'),
        ('identifiers', 'identifier'), ('im_handles', 'im'),
        ('urls', 'url'), ('person_org_role', 'por'),
        ('person_person_relation', 'ppr'), ('org_org_relation', 'oor'),
        ('facts', 'fact'), ('field_provenance', 'fp'),
        ('interactions', 'interaction'), ('consent_records', 'consent'),
        ('action_event', 'ae'), ('merge_history', 'mh'),
    ]
    for tbl, prefix in tenant_tables:
        op.execute(f"""
            CREATE POLICY {prefix}_select_{tbl} ON {tbl} FOR SELECT TO contact_ops_app
                USING (tenant_id = current_tenant_id())
        """)
        op.execute(f"""
            CREATE POLICY {prefix}_modify_{tbl} ON {tbl} FOR ALL TO contact_ops_app
                USING (tenant_id = current_tenant_id())
                WITH CHECK (tenant_id = current_tenant_id())
        """)

    # person_tenant_membership and organization_tenant_membership RLS
    op.execute("""
        CREATE POLICY ptm_select ON person_tenant_membership FOR SELECT TO contact_ops_app
            USING (tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY ptm_modify ON person_tenant_membership FOR ALL TO contact_ops_app
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY otm_select ON organization_tenant_membership FOR SELECT TO contact_ops_app
            USING (tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY otm_modify ON organization_tenant_membership FOR ALL TO contact_ops_app
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
    """)

    # HIPAA cross-tenant merge fence trigger
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_hipaa_merge() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            kept_owner uuid;
            removed_owner uuid;
        BEGIN
            IF NEW.entity_type = 'person' THEN
                SELECT canonical_owner_tenant_id INTO kept_owner
                    FROM persons WHERE id = NEW.kept_id;
                SELECT canonical_owner_tenant_id INTO removed_owner
                    FROM persons WHERE id = NEW.removed_id;
            ELSE
                SELECT canonical_owner_tenant_id INTO kept_owner
                    FROM organizations WHERE id = NEW.kept_id;
                SELECT canonical_owner_tenant_id INTO removed_owner
                    FROM organizations WHERE id = NEW.removed_id;
            END IF;
            IF kept_owner <> removed_owner
               AND (tenant_is_hipaa(kept_owner) OR tenant_is_hipaa(removed_owner))
            THEN
                RAISE EXCEPTION 'HIPAA fence: cross-tenant merge involving HIPAA tenant requires human-approved override.'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER trg_enforce_hipaa_merge BEFORE INSERT ON merge_history FOR EACH ROW EXECUTE FUNCTION enforce_hipaa_merge()")

    # touch_updated trigger function
    op.execute("""
        CREATE OR REPLACE FUNCTION touch_updated() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at := now();
            NEW.etag := encode(digest(row_to_json(NEW)::text, 'sha256'), 'hex');
            RETURN NEW;
        END $$
    """)
    # Attach to tables that have updated_at + etag columns
    etag_tables = [
        'persons', 'organizations', 'emails', 'phones',
        'postal_addresses', 'person_org_role', 'person_person_relation',
        'org_org_relation', 'person_tenant_membership',
        'organization_tenant_membership',
    ]
    for tbl in etag_tables:
        op.execute(f"""
            CREATE TRIGGER trg_touch_updated_{tbl}
            BEFORE UPDATE ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION touch_updated()
        """)

    # alias rewrite trigger
    op.execute("""
        CREATE OR REPLACE FUNCTION rewrite_alias_person_id() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE canonical uuid;
        BEGIN
            IF NEW.person_id IS NOT NULL THEN
                SELECT current_canonical_id INTO canonical
                    FROM person_alias WHERE alias_id = NEW.person_id;
                IF canonical IS NOT NULL THEN NEW.person_id := canonical; END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_rewrite_alias_person_id
        BEFORE INSERT OR UPDATE ON emails
        FOR EACH ROW EXECUTE FUNCTION rewrite_alias_person_id()
    """)
    op.execute("""
        CREATE TRIGGER trg_rewrite_alias_person_id_phones
        BEFORE INSERT OR UPDATE ON phones
        FOR EACH ROW EXECUTE FUNCTION rewrite_alias_person_id()
    """)
    op.execute("""
        CREATE TRIGGER trg_rewrite_alias_person_id_addr
        BEFORE INSERT OR UPDATE ON postal_addresses
        FOR EACH ROW EXECUTE FUNCTION rewrite_alias_person_id()
    """)
    op.execute("""
        CREATE TRIGGER trg_rewrite_alias_person_id_id
        BEFORE INSERT OR UPDATE ON identifiers
        FOR EACH ROW EXECUTE FUNCTION rewrite_alias_person_id()
    """)
    op.execute("""
        CREATE TRIGGER trg_rewrite_alias_person_id_im
        BEFORE INSERT OR UPDATE ON im_handles
        FOR EACH ROW EXECUTE FUNCTION rewrite_alias_person_id()
    """)

    # Seed default tenants
    op.execute("""
        INSERT INTO tenants (slug, kind, display_name, owner_user_id,
            qdrant_namespace, garage_bucket_prefix)
        VALUES
            ('aaron-stransky-personal', 'personal', 'Aaron Stransky (personal)',
             '00000000-0000-0000-0000-000000000000',
             'contact_ops__aaron-stransky-personal',
             'contact-ops-aaron-stransky-personal'),
            ('magic-unicorn-inc', 'magic_unicorn_internal', 'Magic Unicorn Inc',
             '00000000-0000-0000-0000-000000000000',
             'contact_ops__magic-unicorn-inc',
             'contact-ops-magic-unicorn-inc'),
            ('centerdeep', 'brand', 'Center Deep',
             '00000000-0000-0000-0000-000000000000',
             'contact_ops__centerdeep',
             'contact-ops-centerdeep')
        ON CONFLICT (slug) DO NOTHING
    """)


def downgrade() -> None:
    # Drop seed tenants
    op.execute("DELETE FROM tenants WHERE slug IN ('aaron-stransky-personal','magic-unicorn-inc','centerdeep')")

    # Drop triggers
    for tbl in ['emails', 'phones', 'postal_addresses', 'identifiers', 'im_handles']:
        op.execute(f"DROP TRIGGER IF EXISTS trg_rewrite_alias_person_id{'' if tbl == 'emails' else '_' + tbl} ON {tbl}")

    op.execute("DROP TRIGGER IF EXISTS trg_touch_updated_persons ON persons")
    op.execute("DROP TRIGGER IF EXISTS trg_touch_updated_organizations ON organizations")
    op.execute("DROP TRIGGER IF EXISTS trg_touch_updated_emails ON emails")
    op.execute("DROP TRIGGER IF EXISTS trg_touch_updated_phones ON phones")
    op.execute("DROP TRIGGER IF EXISTS trg_touch_updated_postal_addresses ON postal_addresses")
    op.execute("DROP TRIGGER IF EXISTS trg_touch_updated_person_org_role ON person_org_role")
    op.execute("DROP TRIGGER IF EXISTS trg_touch_updated_person_person_relation ON person_person_relation")
    op.execute("DROP TRIGGER IF EXISTS trg_touch_updated_org_org_relation ON org_org_relation")
    op.execute("DROP TRIGGER IF EXISTS trg_touch_updated_person_tenant_membership ON person_tenant_membership")
    op.execute("DROP TRIGGER IF EXISTS trg_touch_updated_organization_tenant_membership ON organization_tenant_membership")

    op.execute("DROP TRIGGER IF EXISTS trg_enforce_hipaa_merge ON merge_history")

    # Drop policies (drop all for simplicity in downgrade)
    tables_to_rls = [
        'persons', 'organizations', 'emails', 'phones', 'postal_addresses',
        'identifiers', 'im_handles', 'urls', 'photos', 'voice_samples',
        'voice_fingerprints', 'person_org_role', 'person_person_relation',
        'org_org_relation', 'facts', 'field_provenance', 'interactions',
        'consent_records', 'action_event', 'merge_history',
        'person_tenant_membership', 'organization_tenant_membership',
    ]
    for tbl in tables_to_rls:
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP FUNCTION IF EXISTS touch_updated()")
    op.execute("DROP FUNCTION IF EXISTS enforce_hipaa_merge()")
    op.execute("DROP FUNCTION IF EXISTS current_tenant_id()")
    op.execute("DROP FUNCTION IF EXISTS tenant_is_hipaa(uuid)")
    op.execute("DROP FUNCTION IF EXISTS rewrite_alias_person_id()")
