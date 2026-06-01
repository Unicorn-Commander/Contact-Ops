"""phase 4.1a: isolation_mode dial + denormalized owner columns + ratchet

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-01

Behavioral NO-OP at deploy. Adds the per-tenant isolation dial as data, the
denormalized owner-mode columns RLS will read in 0036, and the DB-level ratchet
that keeps HIPAA implying strict. Nothing reads ``isolation_mode`` yet (0036
does), and everything defaults to ``standard`` which reproduces today's RLS
predicates verbatim, so deploying this changes no read or write behavior.

Pieces:
  * tenants.isolation_mode ('strict'|'standard'|'open', default 'standard') +
    tenants.isolation_mode_locked (default false).
  * enforce_isolation_ratchet trigger on tenants: HIPAA implies strict; strict is
    one-way once locked or HIPAA. Enforced at the DB so a direct SQL UPDATE or any
    other code path cannot weaken a regulated tenant (app guards alone are
    bypassable).
  * persons.owner_isolation_mode / organizations.owner_isolation_mode (default
    'standard'): the OWNER tenant's mode denormalized onto the identity row. This
    is what 0036's RESTRICTIVE owner-side fence reads — a LOCAL column, so the
    fence is deterministic, index-friendly, and free of the tenants-RLS recursion
    / NULL-over-deny bug that reading tenants through RLS would cause.
  * sync_owner_isolation (SECURITY DEFINER): maintains owner_isolation_mode on
    INSERT / owner-change of an identity row.
  * propagate_isolation_to_rows (SECURITY DEFINER): on a tenant mode flip, pushes
    the new mode onto that tenant's identity rows.
  * current_tenant_isolation() (SECURITY DEFINER): the viewer's own mode, for
    0036's viewer-side gate/fence.

Backfill: the column DEFAULT 'standard' already populates every existing row
correctly, because at this point every tenant's isolation_mode is 'standard'
(the column was just added with that default in this same migration). A
tenant-specific backfill UPDATE would only matter if a tenant could already be
non-standard here, which it cannot — and it would needlessly churn every row's
etag via the touch_updated trigger. So no explicit backfill.

NOTE on HIPAA tenants: none exist today. If one did, flipping it to strict is an
ACTIVATION step (signed off, one-way) per the design, not done here; this
migration deliberately leaves all tenants 'standard'.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- the dial + lock on tenants -------------------------------------------
    op.execute("""
        ALTER TABLE tenants
            ADD COLUMN isolation_mode text NOT NULL DEFAULT 'standard'
                CHECK (isolation_mode IN ('strict','standard','open'))
    """)
    op.execute("""
        ALTER TABLE tenants
            ADD COLUMN isolation_mode_locked boolean NOT NULL DEFAULT false
    """)

    # --- DB-level HIPAA/strict ratchet ----------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_isolation_ratchet() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- HIPAA implies strict, always.
            IF NEW.hipaa_mode AND NEW.isolation_mode <> 'strict' THEN
                RAISE EXCEPTION 'hipaa_mode requires isolation_mode = strict';
            END IF;
            -- One-way strict ratchet: cannot weaken a locked/HIPAA tenant.
            IF (OLD.isolation_mode = 'strict')
               AND (OLD.isolation_mode_locked OR OLD.hipaa_mode)
               AND NEW.isolation_mode <> 'strict' THEN
                RAISE EXCEPTION 'isolation_mode cannot be weakened on a locked/HIPAA tenant';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_enforce_isolation_ratchet
            BEFORE UPDATE OF isolation_mode, isolation_mode_locked, hipaa_mode ON tenants
            FOR EACH ROW EXECUTE FUNCTION enforce_isolation_ratchet()
    """)

    # --- denormalized owner-mode columns on identity rows ---------------------
    op.execute("""
        ALTER TABLE persons
            ADD COLUMN owner_isolation_mode text NOT NULL DEFAULT 'standard'
                CHECK (owner_isolation_mode IN ('strict','standard','open'))
    """)
    op.execute("""
        ALTER TABLE organizations
            ADD COLUMN owner_isolation_mode text NOT NULL DEFAULT 'standard'
                CHECK (owner_isolation_mode IN ('strict','standard','open'))
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION sync_owner_isolation() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        BEGIN
            NEW.owner_isolation_mode := coalesce(
                (SELECT isolation_mode FROM tenants WHERE id = NEW.canonical_owner_tenant_id),
                'standard'
            );
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_sync_owner_isolation_persons
            BEFORE INSERT OR UPDATE OF canonical_owner_tenant_id ON persons
            FOR EACH ROW EXECUTE FUNCTION sync_owner_isolation()
    """)
    op.execute("""
        CREATE TRIGGER trg_sync_owner_isolation_orgs
            BEFORE INSERT OR UPDATE OF canonical_owner_tenant_id ON organizations
            FOR EACH ROW EXECUTE FUNCTION sync_owner_isolation()
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION propagate_isolation_to_rows() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        BEGIN
            IF NEW.isolation_mode IS DISTINCT FROM OLD.isolation_mode THEN
                UPDATE persons       SET owner_isolation_mode = NEW.isolation_mode
                    WHERE canonical_owner_tenant_id = NEW.id;
                UPDATE organizations SET owner_isolation_mode = NEW.isolation_mode
                    WHERE canonical_owner_tenant_id = NEW.id;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_propagate_isolation
            AFTER UPDATE OF isolation_mode ON tenants
            FOR EACH ROW EXECUTE FUNCTION propagate_isolation_to_rows()
    """)

    # --- viewer's own mode (for 0036's viewer gate/fence) ---------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION current_tenant_isolation() RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
            SELECT coalesce(
                (SELECT isolation_mode FROM tenants WHERE id = current_tenant_id()),
                'standard'
            )
        $$
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS current_tenant_isolation()")
    op.execute("DROP TRIGGER IF EXISTS trg_propagate_isolation ON tenants")
    op.execute("DROP FUNCTION IF EXISTS propagate_isolation_to_rows()")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_owner_isolation_orgs ON organizations")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_owner_isolation_persons ON persons")
    op.execute("DROP FUNCTION IF EXISTS sync_owner_isolation()")
    op.execute("DROP TRIGGER IF EXISTS trg_enforce_isolation_ratchet ON tenants")
    op.execute("DROP FUNCTION IF EXISTS enforce_isolation_ratchet()")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS owner_isolation_mode")
    op.execute("ALTER TABLE persons DROP COLUMN IF EXISTS owner_isolation_mode")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS isolation_mode_locked")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS isolation_mode")
