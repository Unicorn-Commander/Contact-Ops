"""phase 4.2: isolation-aware RLS rewrite (gated SELECT + dual RESTRICTIVE fences)

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-01

The ONLY behavior-bearing migration of Phase 4 — and still a NO-OP at deploy,
because every tenant is 'standard' and every identity row's owner_isolation_mode
is 'standard', which makes the gate always-true and both fences always-pass, so
the effective predicates are byte-equivalent to 0015/0033 until a tenant is
flipped (a separate, signed-off activation step).

What changes:
  * persons / organizations SELECT: the owner-OR-(membership|acl) form, with the
    membership+acl branch GATED behind `current_tenant_isolation() <> 'strict'`
    (a strict VIEWER never widens) and the acl-grant read branch added (§3.3).
  * persons / organizations get TWO RESTRICTIVE fences (§3.4):
      - owner-side: a strict-OWNED row is visible only to its owner. Reads the
        LOCAL denormalized owner_isolation_mode column (0034) — deterministic,
        index-friendly, no tenants-RLS recursion / NULL-over-deny.
      - viewer-side: a strict VIEWER only ever sees its own-owned rows.
  * The 10 parent-scoped child tables + person_alias/organization_alias: their
    SELECT becomes a bare parent-visibility EXISTS (which is RLS-subject, so it
    inherits the parent's full gated+fenced isolation — single source of truth),
    plus a parent-visibility RESTRICTIVE fence so no future permissive policy can
    widen them. This is a deliberate simplification of the design's per-child
    owner_isolation_mode walk: "child visible iff parent visible" is a stronger,
    drift-proof guarantee and propagates BOTH the owner-strict and viewer-strict
    behaviors from persons/organizations.
  * acl_grant_grantee_subject_idx (§2.6) to back the per-record acl EXISTS.

NOT touched: writes (persons_modify/insert stay owner-only WITH CHECK); the
non-identity tenant tables (facts, interactions, media_assets, consent_records,
merge_history, data_intel_link, graph_sync_outbox, crisis_ops_entity_link,
person_person_relation, org_org_relation) which keep plain tenant equality and
never expose a cross-tenant branch; field_provenance (tenant-local via
action_event); enforce_hipaa_merge.

Generators are inline (self-contained migration). down() restores the verbatim
0015 identity/child SELECT forms and the verbatim 0033 alias SELECT forms; a
pg_policies snapshot-equality test (run separately) asserts down() lands exactly
on the 0035 state.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ROLES = "contact_ops_app, contact_ops_ro, contact_ops_audit"

# (table, membership table, membership fk, acl subject_kind)
_IDENTITY = [
    ("persons", "person_tenant_membership", "person_id", "person"),
    ("organizations", "organization_tenant_membership", "organization_id", "org"),
]
# the 10 parent-scoped child tables: (table, policy_prefix, person_col, org_col)
_CHILDREN = [
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
# (alias table, owner table, owner fk column on alias)
_ALIASES = [
    ("person_alias", "persons", "current_canonical_id"),
    ("organization_alias", "organizations", "current_canonical_id"),
]


# ---- new (0036) generators ------------------------------------------------
def _gated_identity_select(table: str, membership_tbl: str, fk: str, acl_kind: str) -> str:
    return f"""
        canonical_owner_tenant_id = current_tenant_id()
        OR (
            current_tenant_isolation() <> 'strict'
            AND (
                EXISTS (
                    SELECT 1 FROM {membership_tbl} m
                    WHERE m.{fk} = {table}.id
                      AND m.tenant_id = current_tenant_id()
                      AND m.visibility <> 'archived'
                )
                OR EXISTS (
                    SELECT 1 FROM acl_grant g
                    WHERE g.subject_kind = '{acl_kind}'
                      AND g.subject_id = {table}.id
                      AND g.grantee_tenant_id = current_tenant_id()
                      AND g.revoked_at IS NULL
                      AND (g.expires_at IS NULL OR g.expires_at > now())
                )
            )
        )
    """


def _parent_visible(table: str, person_col: str | None, org_col: str | None) -> str:
    clauses = []
    if person_col:
        clauses.append(f"EXISTS (SELECT 1 FROM persons p WHERE p.id = {table}.{person_col})")
    if org_col:
        clauses.append(f"EXISTS (SELECT 1 FROM organizations o WHERE o.id = {table}.{org_col})")
    return " OR ".join(clauses)


def upgrade() -> None:
    # === persons / organizations: gated SELECT + dual RESTRICTIVE fences ======
    for table, membership_tbl, fk, acl_kind in _IDENTITY:
        op.execute(f"DROP POLICY {table}_select ON {table}")
        op.execute(f"""
            CREATE POLICY {table}_select ON {table} FOR SELECT
                TO {_ROLES}
                USING ({_gated_identity_select(table, membership_tbl, fk, acl_kind)})
        """)
        # owner-side fence: a strict-OWNED row is visible only to its owner.
        op.execute(f"""
            CREATE POLICY {table}_owner_strict_fence ON {table} AS RESTRICTIVE FOR ALL
                TO {_ROLES}
                USING (owner_isolation_mode <> 'strict' OR canonical_owner_tenant_id = current_tenant_id())
        """)
        # viewer-side fence: a strict VIEWER only ever sees its own-owned rows.
        op.execute(f"""
            CREATE POLICY {table}_viewer_strict_fence ON {table} AS RESTRICTIVE FOR ALL
                TO {_ROLES}
                USING (current_tenant_isolation() <> 'strict' OR canonical_owner_tenant_id = current_tenant_id())
        """)

    # === 10 child tables: parent-visibility SELECT + parent-visibility fence ===
    for table, prefix, person_col, org_col in _CHILDREN:
        visible = _parent_visible(table, person_col, org_col)
        op.execute(f"DROP POLICY {prefix}_select ON {table}")
        op.execute(f"""
            CREATE POLICY {prefix}_select ON {table} FOR SELECT
                TO {_ROLES}
                USING ({visible})
        """)
        op.execute(f"""
            CREATE POLICY {prefix}_parent_fence ON {table} AS RESTRICTIVE FOR ALL
                TO {_ROLES}
                USING ({visible})
        """)

    # === alias tables: parent-visibility SELECT + fence (replaces 0033 form) ===
    for alias_tbl, owner_tbl, fk in _ALIASES:
        owner_alias = "p" if owner_tbl == "persons" else "o"
        visible = (
            f"EXISTS (SELECT 1 FROM {owner_tbl} {owner_alias} "
            f"WHERE {owner_alias}.id = {alias_tbl}.{fk})"
        )
        op.execute(f"DROP POLICY {alias_tbl}_select ON {alias_tbl}")
        op.execute(f"""
            CREATE POLICY {alias_tbl}_select ON {alias_tbl} FOR SELECT
                TO {_ROLES}
                USING ({visible})
        """)
        op.execute(f"""
            CREATE POLICY {alias_tbl}_parent_fence ON {alias_tbl} AS RESTRICTIVE FOR ALL
                TO {_ROLES}
                USING ({visible})
        """)

    # === backing index for the acl-grant EXISTS in identity SELECT (§2.6) ======
    op.execute("""
        CREATE INDEX acl_grant_grantee_subject_idx
            ON acl_grant (grantee_tenant_id, subject_kind, subject_id)
            WHERE revoked_at IS NULL AND grantee_tenant_id IS NOT NULL
    """)


# ---- down(): restore the VERBATIM pre-0036 (0035) policy forms -------------
# These reproduce 0015 (identity + children) and 0033 (aliases) byte-for-byte;
# a pg_policies snapshot-equality test asserts down() lands exactly on 0035.
def _legacy_0015_parent_select_scope(table: str, person_column: str | None, org_column: str | None) -> str:
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


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS acl_grant_grantee_subject_idx")

    # aliases: drop 0036 policies, restore the 0033 owner-OR-membership form.
    _alias_0033 = [
        ("person_alias", "persons", "person_tenant_membership", "person_id"),
        ("organization_alias", "organizations", "organization_tenant_membership", "organization_id"),
    ]
    for alias_tbl, owner_tbl, membership_tbl, membership_fk in _alias_0033:
        op.execute(f"DROP POLICY IF EXISTS {alias_tbl}_parent_fence ON {alias_tbl}")
        op.execute(f"DROP POLICY {alias_tbl}_select ON {alias_tbl}")
        op.execute(f"""
            CREATE POLICY {alias_tbl}_select ON {alias_tbl} FOR SELECT
                TO {_ROLES}
                USING (
                    EXISTS (
                        SELECT 1 FROM {owner_tbl} o
                        WHERE o.id = {alias_tbl}.current_canonical_id
                          AND (
                              o.canonical_owner_tenant_id = current_tenant_id()
                              OR EXISTS (
                                  SELECT 1 FROM {membership_tbl} m
                                  WHERE m.{membership_fk} = o.id
                                    AND m.tenant_id = current_tenant_id()
                                    AND m.visibility <> 'archived'
                              )
                          )
                    )
                )
        """)

    # children: drop 0036 policies, restore the verbatim 0015 _parent_select_scope.
    for table, prefix, person_col, org_col in _CHILDREN:
        op.execute(f"DROP POLICY IF EXISTS {prefix}_parent_fence ON {table}")
        op.execute(f"DROP POLICY {prefix}_select ON {table}")
        op.execute(f"""
            CREATE POLICY {prefix}_select ON {table} FOR SELECT
                TO {_ROLES}
                USING ({_legacy_0015_parent_select_scope(table, person_col, org_col)})
        """)

    # persons / organizations: drop fences + gated select, restore verbatim 0015.
    for table, membership_tbl, fk, _acl_kind in _IDENTITY:
        op.execute(f"DROP POLICY IF EXISTS {table}_viewer_strict_fence ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_owner_strict_fence ON {table}")
        op.execute(f"DROP POLICY {table}_select ON {table}")
        op.execute(f"""
            CREATE POLICY {table}_select ON {table} FOR SELECT
                TO {_ROLES}
                USING (
                    canonical_owner_tenant_id = current_tenant_id()
                    OR EXISTS (
                        SELECT 1
                        FROM {membership_tbl} m
                        WHERE m.{fk} = {table}.id
                          AND m.tenant_id = current_tenant_id()
                          AND m.visibility <> 'archived'
                    )
                )
        """)
