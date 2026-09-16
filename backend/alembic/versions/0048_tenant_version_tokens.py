"""Initialize tenant versions on every insert and rotate them on writes.

Opaque random versions prevent ABA reuse. Existing nonempty tokens stay valid;
only rows with the unusable legacy empty token are backfilled. Provisioning SQL
and ORM inserts both use this trigger, including personal-workspace onboarding.
"""

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION touch_tenant_version() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public, pg_temp
        AS $$
        BEGIN
            NEW.updated_at := clock_timestamp();
            NEW.etag := encode(gen_random_bytes(32), 'hex');
            RETURN NEW;
        END
        $$
    """)
    op.execute("DROP TRIGGER trg_touch_updated_tenants ON tenants")
    op.execute("""
        CREATE TRIGGER trg_touch_updated_tenants
        BEFORE INSERT OR UPDATE ON tenants
        FOR EACH ROW EXECUTE FUNCTION touch_tenant_version()
    """)
    op.execute("UPDATE tenants SET etag = etag WHERE etag = ''")
    op.execute("ALTER TABLE tenants ADD CONSTRAINT ck_tenants_etag_nonempty CHECK (etag <> '')")


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP CONSTRAINT ck_tenants_etag_nonempty")
    op.execute("DROP TRIGGER trg_touch_updated_tenants ON tenants")
    op.execute("""
        CREATE TRIGGER trg_touch_updated_tenants
        BEFORE UPDATE ON tenants
        FOR EACH ROW EXECUTE FUNCTION touch_updated()
    """)
    op.execute("DROP FUNCTION touch_tenant_version()")
