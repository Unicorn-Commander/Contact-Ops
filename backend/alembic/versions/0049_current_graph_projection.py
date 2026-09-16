"""Queue current contact/organization projections atomically; no automatic backfill."""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE UNIQUE INDEX gso_current_projection_pending
      ON graph_sync_outbox(tenant_id, entity_kind, entity_id)
      WHERE status = 'pending' AND payload->>'projection' = 'current_affiliations_v1'
    """)
    op.execute("""
CREATE FUNCTION enqueue_current_graph(p_tid uuid, p_kind text, p_eid uuid) RETURNS void
    LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
    BEGIN
      IF p_kind NOT IN ('person','organization') THEN
        RAISE EXCEPTION 'unsupported graph source';
      END IF;
      INSERT INTO graph_sync_outbox(entity_kind,entity_id,op,payload,tenant_id,graph_name)
      SELECT p_kind,p_eid,'upsert','{"projection":"current_affiliations_v1"}'::jsonb,p_tid,
        COALESCE(NULLIF(t.graph_name,''),'contact_ops__' || left(trim(both '_' from
          regexp_replace(lower(replace(t.slug,'-','_')),'[^a-z0-9_]+','_','g')),63))
      FROM tenants t WHERE t.id=p_tid
      ON CONFLICT (tenant_id,entity_kind,entity_id)
        WHERE status='pending' AND payload->>'projection'='current_affiliations_v1'
        DO NOTHING;
    END $$
    """)
    op.execute("""
CREATE FUNCTION queue_current_graph_node() RETURNS trigger
    LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
    DECLARE tid uuid; eid uuid; pid uuid; kind text;
    BEGIN
      kind := CASE WHEN TG_TABLE_NAME='persons' THEN 'person' ELSE 'organization' END;
      IF TG_OP <> 'INSERT' THEN
        PERFORM enqueue_current_graph(OLD.canonical_owner_tenant_id,kind,OLD.id);
      END IF;
      IF TG_OP <> 'DELETE' THEN
        PERFORM enqueue_current_graph(NEW.canonical_owner_tenant_id,kind,NEW.id);
      END IF;


      IF kind='organization' THEN
        IF TG_OP='DELETE' THEN tid:=OLD.canonical_owner_tenant_id; eid:=OLD.id;
        ELSE tid:=NEW.canonical_owner_tenant_id; eid:=NEW.id; END IF;
        FOR pid IN SELECT DISTINCT p.id FROM person_org_role r JOIN persons p ON p.id=r.person_id
          WHERE r.organization_id=eid AND p.canonical_owner_tenant_id=tid
        LOOP PERFORM enqueue_current_graph(tid,'person',pid); END LOOP;
      END IF;
      RETURN NULL;
    END $$
    """)
    op.execute("""
CREATE FUNCTION queue_current_graph_employment() RETURNS trigger
    LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
    DECLARE p record;
    BEGIN
      IF TG_OP <> 'INSERT' THEN
        FOR p IN SELECT id,canonical_owner_tenant_id FROM persons WHERE id=OLD.person_id
        LOOP PERFORM enqueue_current_graph(p.canonical_owner_tenant_id,'person',p.id); END LOOP;
      END IF;
      IF TG_OP <> 'DELETE' THEN
        FOR p IN SELECT id,canonical_owner_tenant_id FROM persons WHERE id=NEW.person_id
        LOOP PERFORM enqueue_current_graph(p.canonical_owner_tenant_id,'person',p.id); END LOOP;
      END IF;
      RETURN NULL;
    END $$
    """)
    op.execute("""
CREATE TRIGGER trg_current_graph_person AFTER INSERT OR UPDATE OR DELETE ON persons
      FOR EACH ROW EXECUTE FUNCTION queue_current_graph_node()
    """)
    op.execute("""
CREATE TRIGGER trg_current_graph_org AFTER INSERT OR UPDATE OR DELETE ON organizations
      FOR EACH ROW EXECUTE FUNCTION queue_current_graph_node()
    """)
    op.execute("""
CREATE TRIGGER trg_current_graph_employment AFTER INSERT OR UPDATE OR DELETE ON person_org_role
      FOR EACH ROW EXECUTE FUNCTION queue_current_graph_employment()
    """)


def downgrade():
    op.execute("""
DROP TRIGGER trg_current_graph_employment ON person_org_role
    """)
    op.execute("""
DROP TRIGGER trg_current_graph_org ON organizations
    """)
    op.execute("""
DROP TRIGGER trg_current_graph_person ON persons
    """)
    op.execute("""
DROP FUNCTION queue_current_graph_employment()
    """)
    op.execute("""
DROP FUNCTION queue_current_graph_node()
    """)
    op.execute("""
DROP FUNCTION enqueue_current_graph(uuid,text,uuid)
    """)
    op.execute("""
DROP INDEX gso_current_projection_pending
    """)
    op.execute("""
DELETE FROM graph_sync_outbox WHERE payload->>'projection'='current_affiliations_v1'
    """)
