"""rls_policies_hipaa_triggers_etag

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RLS_TABLES = [
    "tenants",
    "persons",
    "organizations",
    "emails",
    "phones",
    "postal_addresses",
    "identifiers",
    "im_handles",
    "urls",
    "media_assets",
    "photos",
    "voice_samples",
    "voice_fingerprints",
    "person_org_role",
    "person_person_relation",
    "org_org_relation",
    "facts",
    "field_provenance",
    "interactions",
    "consent_records",
    "action_event",
    "merge_history",
    "person_tenant_membership",
    "organization_tenant_membership",
    "agent_registry",
    "agent_calibration",
    "data_intel_link",
    "graph_sync_outbox",
]


def _parent_scope(table: str, person_column: str | None, org_column: str | None) -> str:
    clauses = []
    if person_column:
        clauses.append(f"""
            EXISTS (
                SELECT 1
                FROM persons p
                WHERE p.id = {table}.{person_column}
                  AND p.canonical_owner_tenant_id = current_tenant_id()
            )
        """)
    if org_column:
        clauses.append(f"""
            EXISTS (
                SELECT 1
                FROM organizations o
                WHERE o.id = {table}.{org_column}
                  AND o.canonical_owner_tenant_id = current_tenant_id()
            )
        """)
    return " OR ".join(clauses)


def _parent_select_scope(table: str, person_column: str | None, org_column: str | None) -> str:
    clauses = []
    if person_column:
        clauses.append(f"""
            EXISTS (
                SELECT 1
                FROM persons p
                WHERE p.id = {table}.{person_column}
                  AND (
                      p.canonical_owner_tenant_id = current_tenant_id()
                      OR EXISTS (
                          SELECT 1
                          FROM person_tenant_membership m
                          WHERE m.person_id = p.id
                            AND m.tenant_id = current_tenant_id()
                            AND m.visibility <> 'archived'
                      )
                  )
            )
        """)
    if org_column:
        clauses.append(f"""
            EXISTS (
                SELECT 1
                FROM organizations o
                WHERE o.id = {table}.{org_column}
                  AND (
                      o.canonical_owner_tenant_id = current_tenant_id()
                      OR EXISTS (
                          SELECT 1
                          FROM organization_tenant_membership m
                          WHERE m.organization_id = o.id
                            AND m.tenant_id = current_tenant_id()
                            AND m.visibility <> 'archived'
                      )
                  )
            )
        """)
    return " OR ".join(clauses)


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION tenant_is_hipaa(t uuid) RETURNS boolean
        LANGUAGE sql STABLE
        AS $$ SELECT coalesce((SELECT hipaa_mode FROM tenants WHERE id = t), false) $$
    """)

    for tbl in RLS_TABLES:
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY tenants_select ON tenants FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (id = current_tenant_id() OR parent_tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY tenants_modify ON tenants FOR ALL TO contact_ops_app
            USING (id = current_tenant_id() OR parent_tenant_id = current_tenant_id())
            WITH CHECK (id = current_tenant_id() OR parent_tenant_id = current_tenant_id())
    """)

    op.execute("""
        CREATE POLICY persons_select ON persons FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (
                canonical_owner_tenant_id = current_tenant_id()
                OR EXISTS (
                    SELECT 1
                    FROM person_tenant_membership m
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

    op.execute("""
        CREATE POLICY organizations_select ON organizations FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (
                canonical_owner_tenant_id = current_tenant_id()
                OR EXISTS (
                    SELECT 1
                    FROM organization_tenant_membership m
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

    parent_child_tables = [
        ("emails", "emails", "person_id", "organization_id"),
        ("phones", "phones", "person_id", "organization_id"),
        ("postal_addresses", "postal_addresses", "person_id", "organization_id"),
        ("identifiers", "identifiers", "person_id", "organization_id"),
        ("im_handles", "im_handles", "person_id", None),
        ("urls", "urls", "person_id", "organization_id"),
        ("person_org_role", "por", "person_id", "organization_id"),
        ("photos", "photos", "person_id", None),
        ("voice_samples", "voice_samples", "person_id", None),
        ("voice_fingerprints", "voice_fp", "person_id", None),
    ]
    for table, prefix, person_column, org_column in parent_child_tables:
        select_scope = _parent_select_scope(table, person_column, org_column)
        modify_scope = _parent_scope(table, person_column, org_column)
        op.execute(f"""
            CREATE POLICY {prefix}_select ON {table} FOR SELECT
                TO contact_ops_app, contact_ops_ro, contact_ops_audit
                USING ({select_scope})
        """)
        op.execute(f"""
            CREATE POLICY {prefix}_modify ON {table} FOR ALL TO contact_ops_app
                USING ({modify_scope})
                WITH CHECK ({modify_scope})
        """)

    tenant_tables = [
        ("media_assets", "media_assets"),
        ("facts", "facts"),
        ("interactions", "interactions"),
        ("consent_records", "consent"),
        ("action_event", "ae"),
        ("merge_history", "merge_history"),
        ("data_intel_link", "dil"),
        ("graph_sync_outbox", "gso"),
    ]
    for table, prefix in tenant_tables:
        op.execute(f"""
            CREATE POLICY {prefix}_select ON {table} FOR SELECT
                TO contact_ops_app, contact_ops_ro, contact_ops_audit
                USING (tenant_id = current_tenant_id())
        """)
        op.execute(f"""
            CREATE POLICY {prefix}_modify ON {table} FOR ALL TO contact_ops_app
                USING (tenant_id = current_tenant_id())
                WITH CHECK (tenant_id = current_tenant_id())
        """)

    op.execute("""
        CREATE POLICY ae_audit_insert ON action_event FOR INSERT TO contact_ops_audit
            WITH CHECK (tenant_id = current_tenant_id())
    """)

    op.execute("""
        CREATE POLICY ppr_select ON person_person_relation FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (tenant_visibility = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY ppr_modify ON person_person_relation FOR ALL TO contact_ops_app
            USING (tenant_visibility = current_tenant_id())
            WITH CHECK (tenant_visibility = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY oor_select ON org_org_relation FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (tenant_visibility = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY oor_modify ON org_org_relation FOR ALL TO contact_ops_app
            USING (tenant_visibility = current_tenant_id())
            WITH CHECK (tenant_visibility = current_tenant_id())
    """)

    op.execute("""
        CREATE POLICY fp_select ON field_provenance FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (
                EXISTS (
                    SELECT 1
                    FROM action_event ae
                    WHERE ae.event_id = field_provenance.set_by_event_id
                      AND ae.tenant_id = current_tenant_id()
                )
            )
    """)
    op.execute("""
        CREATE POLICY fp_modify ON field_provenance FOR ALL TO contact_ops_app
            USING (
                EXISTS (
                    SELECT 1
                    FROM action_event ae
                    WHERE ae.event_id = field_provenance.set_by_event_id
                      AND ae.tenant_id = current_tenant_id()
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1
                    FROM action_event ae
                    WHERE ae.event_id = field_provenance.set_by_event_id
                      AND ae.tenant_id = current_tenant_id()
                )
            )
    """)

    op.execute("""
        CREATE POLICY ptm_select ON person_tenant_membership FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY ptm_modify ON person_tenant_membership FOR ALL TO contact_ops_app
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY otm_select ON organization_tenant_membership FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY otm_modify ON organization_tenant_membership FOR ALL TO contact_ops_app
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
    """)

    op.execute("""
        CREATE POLICY agent_registry_select ON agent_registry FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (owning_tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY agent_registry_modify ON agent_registry FOR ALL TO contact_ops_app
            USING (owning_tenant_id = current_tenant_id())
            WITH CHECK (owning_tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY agent_calibration_select ON agent_calibration FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (
                EXISTS (
                    SELECT 1
                    FROM agent_registry ar
                    WHERE ar.id = agent_calibration.agent_id
                      AND ar.owning_tenant_id = current_tenant_id()
                )
            )
    """)
    op.execute("""
        CREATE POLICY agent_calibration_modify ON agent_calibration FOR ALL TO contact_ops_app
            USING (
                EXISTS (
                    SELECT 1
                    FROM agent_registry ar
                    WHERE ar.id = agent_calibration.agent_id
                      AND ar.owning_tenant_id = current_tenant_id()
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1
                    FROM agent_registry ar
                    WHERE ar.id = agent_calibration.agent_id
                      AND ar.owning_tenant_id = current_tenant_id()
                )
            )
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_hipaa_merge() RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            entity_type text;
            kept_id uuid;
            removed_id uuid;
            kept_owner uuid;
            removed_owner uuid;
        BEGIN
            IF TG_TABLE_NAME = 'person_alias' THEN
                entity_type := 'person';
                kept_id := NEW.current_canonical_id;
                removed_id := NEW.alias_id;
            ELSE
                entity_type := NEW.entity_type;
                kept_id := NEW.kept_id;
                removed_id := NEW.removed_id;
            END IF;

            IF entity_type = 'person' THEN
                SELECT canonical_owner_tenant_id INTO kept_owner
                    FROM persons WHERE id = kept_id;
                SELECT canonical_owner_tenant_id INTO removed_owner
                    FROM persons WHERE id = removed_id;
            ELSE
                SELECT canonical_owner_tenant_id INTO kept_owner
                    FROM organizations WHERE id = kept_id;
                SELECT canonical_owner_tenant_id INTO removed_owner
                    FROM organizations WHERE id = removed_id;
            END IF;

            IF kept_owner IS NULL OR removed_owner IS NULL THEN
                RAISE EXCEPTION 'HIPAA fence: owner not found'
                    USING ERRCODE = 'check_violation';
            END IF;

            IF kept_owner <> removed_owner
               AND (tenant_is_hipaa(kept_owner) OR tenant_is_hipaa(removed_owner))
            THEN
                RAISE EXCEPTION 'HIPAA fence: cross-tenant merge involving HIPAA tenant requires human-approved override.'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER trg_enforce_hipaa_merge
        BEFORE INSERT OR UPDATE ON merge_history
        FOR EACH ROW EXECUTE FUNCTION enforce_hipaa_merge()
    """)
    op.execute("""
        CREATE TRIGGER trg_enforce_hipaa_person_alias
        BEFORE INSERT OR UPDATE ON person_alias
        FOR EACH ROW EXECUTE FUNCTION enforce_hipaa_merge()
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION touch_updated() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            next_row jsonb;
        BEGIN
            next_row := to_jsonb(NEW);
            IF next_row ? 'updated_at' THEN
                next_row := jsonb_set(next_row, '{updated_at}', to_jsonb(now()));
            END IF;
            IF next_row ? 'etag' THEN
                next_row := jsonb_set(
                    next_row,
                    '{etag}',
                    to_jsonb(encode(digest((next_row - 'etag' - 'updated_at')::text, 'sha256'), 'hex'))
                );
            END IF;
            NEW := jsonb_populate_record(NEW, next_row);
            RETURN NEW;
        END
        $$
    """)
    touch_tables = [
        "tenants",
        "persons",
        "organizations",
        "emails",
        "phones",
        "postal_addresses",
        "person_org_role",
        "person_person_relation",
        "org_org_relation",
        "person_tenant_membership",
        "organization_tenant_membership",
        "agent_registry",
    ]
    for tbl in touch_tables:
        op.execute(f"""
            CREATE TRIGGER trg_touch_updated_{tbl}
            BEFORE UPDATE ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION touch_updated()
        """)

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
        END
        $$
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


def downgrade() -> None:
    alias_triggers = [
        ("emails", "trg_rewrite_alias_person_id"),
        ("phones", "trg_rewrite_alias_person_id_phones"),
        ("postal_addresses", "trg_rewrite_alias_person_id_addr"),
        ("identifiers", "trg_rewrite_alias_person_id_id"),
        ("im_handles", "trg_rewrite_alias_person_id_im"),
    ]
    for table, trigger in alias_triggers:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    touch_tables = [
        "tenants",
        "persons",
        "organizations",
        "emails",
        "phones",
        "postal_addresses",
        "person_org_role",
        "person_person_relation",
        "org_org_relation",
        "person_tenant_membership",
        "organization_tenant_membership",
        "agent_registry",
    ]
    for tbl in touch_tables:
        op.execute(f"DROP TRIGGER IF EXISTS trg_touch_updated_{tbl} ON {tbl}")

    op.execute("DROP TRIGGER IF EXISTS trg_enforce_hipaa_person_alias ON person_alias")
    op.execute("DROP TRIGGER IF EXISTS trg_enforce_hipaa_merge ON merge_history")

    policy_drops = [
        ("tenants", ["tenants_select", "tenants_modify"]),
        ("persons", ["persons_select", "persons_modify", "persons_insert"]),
        ("organizations", ["organizations_select", "organizations_modify", "organizations_insert"]),
        ("emails", ["emails_select", "emails_modify"]),
        ("phones", ["phones_select", "phones_modify"]),
        ("postal_addresses", ["postal_addresses_select", "postal_addresses_modify"]),
        ("identifiers", ["identifiers_select", "identifiers_modify"]),
        ("im_handles", ["im_handles_select", "im_handles_modify"]),
        ("urls", ["urls_select", "urls_modify"]),
        ("person_org_role", ["por_select", "por_modify"]),
        ("photos", ["photos_select", "photos_modify"]),
        ("voice_samples", ["voice_samples_select", "voice_samples_modify"]),
        ("voice_fingerprints", ["voice_fp_select", "voice_fp_modify"]),
        ("media_assets", ["media_assets_select", "media_assets_modify"]),
        ("facts", ["facts_select", "facts_modify"]),
        ("interactions", ["interactions_select", "interactions_modify"]),
        ("consent_records", ["consent_select", "consent_modify"]),
        ("action_event", ["ae_select", "ae_modify", "ae_audit_insert"]),
        ("merge_history", ["merge_history_select", "merge_history_modify"]),
        ("data_intel_link", ["dil_select", "dil_modify"]),
        ("graph_sync_outbox", ["gso_select", "gso_modify"]),
        ("person_person_relation", ["ppr_select", "ppr_modify"]),
        ("org_org_relation", ["oor_select", "oor_modify"]),
        ("field_provenance", ["fp_select", "fp_modify"]),
        ("person_tenant_membership", ["ptm_select", "ptm_modify"]),
        ("organization_tenant_membership", ["otm_select", "otm_modify"]),
        ("agent_registry", ["agent_registry_select", "agent_registry_modify"]),
        ("agent_calibration", ["agent_calibration_select", "agent_calibration_modify"]),
    ]
    for table, policies in policy_drops:
        for policy in policies:
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")

    for tbl in RLS_TABLES:
        op.execute(f"ALTER TABLE {tbl} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP FUNCTION IF EXISTS touch_updated()")
    op.execute("DROP FUNCTION IF EXISTS enforce_hipaa_merge()")
    op.execute("DROP FUNCTION IF EXISTS rewrite_alias_person_id()")
    op.execute("DROP FUNCTION IF EXISTS current_tenant_id()")
    op.execute("DROP FUNCTION IF EXISTS tenant_is_hipaa(uuid)")
