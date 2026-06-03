"""phase 5: suite-identity mirror columns (isolation_tier + status) + ratchet ext

Revision ID: 0039
Revises: 0038
Create Date: 2026-06-03

Behavioral NO-OP at deploy (same posture as 0034, which this mirrors). Adds the
two registry-mirror columns SUITE-IDENTITY §5 mandates so CO's `public.tenants`
projection matches the uc-registry record 1:1, plus extends the EXISTING
`enforce_isolation_ratchet` trigger to also enforce the HIPAA→tier coupling.
Nothing reads `isolation_tier`/`status` at deploy except the new (also-new) read
surface GET /api/v1/workspaces/{id}; both columns default to the value that
reproduces today exactly, so deploying this changes no read or write behavior.

SUITE-IDENTITY §5 — the two orthogonal isolation columns. CO already ships
`isolation_mode` correctly (0034: text CHECK IN ('strict','standard','open')).
CO is MISSING `isolation_tier`; add it (the placement/pricing axis), and add
`status` (the forward lifecycle field; `deprecated_at` stays for back-compat).

Both are TEXT + CHECK, NEVER Postgres ENUM (SUITE-IDENTITY §5: so adding/
reordering values is a cheap CHECK change, avoiding the one-way non-transactional
`ALTER TYPE ... ADD VALUE` trap — the same reasoned choice 0034 made for
isolation_mode).

Pieces:
  * tenants.isolation_tier ('shared'|'premium'|'enterprise'|'dedicated', default
    'shared'). The placement/pricing lever; the registry (ops-center) is its SoR,
    CO mirrors it. Default 'shared' == today's effective placement (RLS-pool).
  * tenants.status ('provisioning'|'active'|'suspended'|'archived'|'deleting',
    default 'active'). The forward lifecycle field; matches the registry record
    so the suspend/archive lifecycle has a home. `deprecated_at` is unchanged and
    remains the legacy archival signal — the §2.2 projection derives a coherent
    `status` from `status` directly (this column), not from `deprecated_at`.
  * enforce_isolation_ratchet EXTENDED (CREATE OR REPLACE of the SAME function +
    widen the trigger's column list to include isolation_tier) for the binding
    coupling ratchet (SUITE-IDENTITY §5):
      - HIPAA ⇒ isolation_tier ≥ premium (PHI never co-resides with non-BAA
        tenants on RLS-only separation). RAISE if NEW.hipaa_mode AND
        NEW.isolation_tier = 'shared'.
      - isolation_tier is promote-only/monotonic (shared→premium→enterprise→
        dedicated): a weakening UPDATE RAISEs. (The existing strict one-way
        isolation_mode ratchet is preserved verbatim.)
    We extend the ONE existing trigger function (not add a second trigger) so the
    coupling stays in lockstep with the strict ratchet and there is a single
    enforcement seam to reason about.

Backfill: the column DEFAULTs already populate every existing row correctly,
because at this point every tenant's isolation_tier is 'shared' (the column was
just added with that default in this same migration) and every non-deprecated
tenant's status is 'active'. There are NO HIPAA tenants today (per 0034's note),
so the new HIPAA→premium coupling cannot reject any existing row's default. A
tenant-specific backfill UPDATE would only churn etags via touch_updated for no
behavioral gain, so none is done — same reasoning 0034 used.

Additive and reversible. down() restores the exact pre-0039 `enforce_isolation_
ratchet` body (the 0034 definition) and drops the two columns. Single alembic
head discipline: down_revision is 0038 (the auto-provision head); this is the
sole child so `alembic heads | wc -l == 1`.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- the two registry-mirror columns (TEXT + CHECK, never ENUM) -----------
    op.execute("""
        ALTER TABLE tenants
            ADD COLUMN isolation_tier text NOT NULL DEFAULT 'shared'
                CHECK (isolation_tier IN ('shared','premium','enterprise','dedicated'))
    """)
    op.execute("""
        ALTER TABLE tenants
            ADD COLUMN status text NOT NULL DEFAULT 'active'
                CHECK (status IN ('provisioning','active','suspended','archived','deleting'))
    """)
    op.execute(
        "COMMENT ON COLUMN tenants.isolation_tier IS "
        "'SUITE-IDENTITY §5 physical-placement axis (shared|premium|enterprise|"
        "dedicated). uc-registry is SoR; CO mirrors. Promote-only. HIPAA ⇒ ≥ premium.'"
    )
    op.execute(
        "COMMENT ON COLUMN tenants.status IS "
        "'SUITE-IDENTITY registry lifecycle (provisioning|active|suspended|"
        "archived|deleting). Forward field; deprecated_at kept for back-compat.'"
    )

    # --- extend the ONE ratchet function for the HIPAA→tier coupling ----------
    # CREATE OR REPLACE the SAME function (0034's body preserved verbatim) + the
    # two new tier rules; widen the trigger's BEFORE UPDATE OF column list to
    # include isolation_tier so a direct tier-weakening UPDATE is also caught.
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_isolation_ratchet() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- HIPAA implies strict, always. (0034, unchanged.)
            IF NEW.hipaa_mode AND NEW.isolation_mode <> 'strict' THEN
                RAISE EXCEPTION 'hipaa_mode requires isolation_mode = strict';
            END IF;
            -- One-way strict ratchet: cannot weaken a locked/HIPAA tenant. (0034.)
            IF (OLD.isolation_mode = 'strict')
               AND (OLD.isolation_mode_locked OR OLD.hipaa_mode)
               AND NEW.isolation_mode <> 'strict' THEN
                RAISE EXCEPTION 'isolation_mode cannot be weakened on a locked/HIPAA tenant';
            END IF;
            -- SUITE-IDENTITY §5 coupling: HIPAA ⇒ isolation_tier ≥ premium.
            -- 'shared' is the only sub-premium tier; PHI must never co-reside
            -- with non-BAA tenants on RLS-only separation.
            IF NEW.hipaa_mode AND NEW.isolation_tier = 'shared' THEN
                RAISE EXCEPTION 'hipaa_mode requires isolation_tier >= premium (not shared)';
            END IF;
            -- isolation_tier is monotonic / promote-only
            -- (shared < premium < enterprise < dedicated). A weakening UPDATE is
            -- rejected at the DB so no code path can demote placement behind the
            -- stable workspace_id.
            IF (
                CASE NEW.isolation_tier
                    WHEN 'shared' THEN 0 WHEN 'premium' THEN 1
                    WHEN 'enterprise' THEN 2 WHEN 'dedicated' THEN 3
                END
              ) < (
                CASE OLD.isolation_tier
                    WHEN 'shared' THEN 0 WHEN 'premium' THEN 1
                    WHEN 'enterprise' THEN 2 WHEN 'dedicated' THEN 3
                END
              ) THEN
                RAISE EXCEPTION 'isolation_tier is promote-only and cannot be weakened';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_enforce_isolation_ratchet ON tenants")
    op.execute("""
        CREATE TRIGGER trg_enforce_isolation_ratchet
            BEFORE UPDATE OF isolation_mode, isolation_mode_locked, hipaa_mode,
                             isolation_tier ON tenants
            FOR EACH ROW EXECUTE FUNCTION enforce_isolation_ratchet()
    """)

    # --- set_default_workspace(): the re-scoped switch-endpoint primitive ------
    # SUITE-IDENTITY Spec 1 §"Contact-Ops": switch-workspace is re-scoped to "set
    # my default (is_default) + UX hint". Flipping is_default spans MULTIPLE
    # tenant_ids for ONE user (clear the prior default row, set the new one), but
    # user_tenant_membership RLS is utm_tenant_scope (writes only where
    # tenant_id = current_tenant_id()) + utm_self (SELECT only). A plain UPDATE
    # from the caller's bound (from-tenant) session therefore could not touch the
    # target's row or the prior-default row. This SECURITY DEFINER function does
    # the cross-tenant default flip ATOMICALLY for the caller's OWN memberships
    # only — same DEFINER posture as user_is_active_member (0037) /
    # provision_personal_workspace (0038).
    #
    # NARROW by construction: it mutates ONLY rows WHERE user_uc_uid = p_uc_uid,
    # and ONLY the is_default flag (+ last_switched_at stamp on the chosen row).
    # It requires an ACTIVE membership for (p_uc_uid, p_workspace_id) — returns
    # false (a no-op) otherwise, so it can never make a non-member's row default.
    # The caller (api/auth.py) ALSO checks membership via user_is_active_member
    # before calling; this is defense in depth, not the sole gate.
    op.execute("""
        CREATE OR REPLACE FUNCTION set_default_workspace(
            p_uc_uid      text,
            p_workspace_id uuid
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
        DECLARE
            v_is_member boolean;
        BEGIN
            -- Must be an active member of the target, else no-op (false).
            SELECT EXISTS (
                SELECT 1 FROM user_tenant_membership
                WHERE user_uc_uid = p_uc_uid
                  AND tenant_id = p_workspace_id
                  AND status = 'active'
            ) INTO v_is_member;
            IF NOT v_is_member THEN
                RETURN false;
            END IF;

            -- Clear any OTHER row that is currently this user's default.
            UPDATE user_tenant_membership
                SET is_default = false, updated_at = now()
                WHERE user_uc_uid = p_uc_uid
                  AND tenant_id <> p_workspace_id
                  AND is_default;

            -- Set the chosen row as default + stamp the switch time.
            UPDATE user_tenant_membership
                SET is_default = true,
                    last_switched_at = now(),
                    updated_at = now()
                WHERE user_uc_uid = p_uc_uid
                  AND tenant_id = p_workspace_id;

            RETURN true;
        END $$
    """)
    op.execute(
        "GRANT EXECUTE ON FUNCTION set_default_workspace(text, uuid) "
        "TO contact_ops_app, contact_ops_ro, contact_ops_audit"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS set_default_workspace(text, uuid)")
    # Restore the EXACT pre-0039 (0034) ratchet body + trigger column list, then
    # drop the two mirror columns. (Drop the trigger before the columns so the
    # BEFORE UPDATE OF isolation_tier reference is gone first.)
    op.execute("DROP TRIGGER IF EXISTS trg_enforce_isolation_ratchet ON tenants")
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
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS status")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS isolation_tier")
