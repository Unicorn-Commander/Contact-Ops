"""phase 4.4: provision_personal_workspace() for public self-serve onboarding

Revision ID: 0040
Revises: 0039
Create Date: 2026-06-03

A SECURITY DEFINER provisioning function the onboarding path calls on a human's
FIRST authenticated request, to materialize their personal workspace + the
admin membership that the §4.4 membership gate then enforces.

Why DEFINER (and why this is the right tool, not a footgun):
  At first-login the session has NO tenant bound yet, so current_tenant_id() is
  NULL. Both `tenants` (tenants_modify, 0015) and `user_tenant_membership`
  (utm_tenant_scope, 0035) are FORCE ROW LEVEL SECURITY with WITH CHECK clauses
  of the shape `... = current_tenant_id()`. A plain INSERT from the app role
  (contact_ops_app) would therefore be REJECTED by RLS — the brand-new tenant's
  id can never equal a NULL current_tenant_id(). DEFINER runs the body as the
  function owner (the migration/bootstrap superuser), which bypasses RLS even
  under FORCE, so the not-yet-bound onboarding session can create exactly its
  own first tenant + admin row atomically. This is the same DEFINER+search_path
  posture as enforce_hipaa_merge (0015) and sync_owner_isolation (0034).

  The function is deliberately NARROW: it only ever creates a kind='personal',
  isolation_mode='standard', hipaa_mode=false workspace owned by the calling
  principal and a single self admin membership for that same principal. It reads
  no other tenant's data and writes nothing it does not own, so the DEFINER
  bypass does not widen the blast radius beyond "your own first workspace".

Concurrency / idempotency / uniqueness (see the report at the bottom of the PR):
  * pg_advisory_xact_lock(hashtext('contactops:provision:'||p_uc_uid)) serializes
    concurrent first-logins for the SAME user. The lock is transaction-scoped, so
    a racing second call blocks until the first COMMITs, then takes the
    already-has-membership fast path and returns the same tenant_id.
  * If an active membership already exists, return its tenant_id (is_default first,
    else any active) — fully idempotent, no second workspace.
  * Slug = sanitized username/email-local/'workspace' + '-' + 6 hex of a fresh
    gen_random_uuid(). The random suffix guarantees practical uniqueness; a bounded
    retry loop + the tenants.slug UNIQUE constraint are the hard backstop.

Additive and inert until the onboarding path calls it. Creating the function
changes no existing read/write behavior; down() drops it.

All free-text inputs (p_username, p_email) are consumed ONLY as plpgsql values
inside parameterized expressions (regexp_replace / lower / ||). There is no
dynamic SQL (no EXECUTE), so username/email cannot inject SQL.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(r"""
        CREATE OR REPLACE FUNCTION provision_personal_workspace(
            p_uc_uid  text,
            p_username text,
            p_email    text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
        DECLARE
            v_existing_tenant uuid;
            v_owner_uuid      uuid;
            v_display_name    text;
            v_base            text;
            v_slug            text;
            v_new_tenant      uuid;
            v_attempt         int := 0;
        BEGIN
            -- (1) Serialize concurrent first-logins for the SAME user. xact-scoped:
            -- a racing caller blocks here until our tx commits, then sees our
            -- membership row via the fast path below and returns the same tenant.
            PERFORM pg_advisory_xact_lock(hashtext('contactops:provision:' || coalesce(p_uc_uid, '')));

            -- (2) Idempotent fast path: if the user already has an active
            -- membership, return that tenant (prefer the default, else any active).
            SELECT tenant_id INTO v_existing_tenant
            FROM user_tenant_membership
            WHERE user_uc_uid = p_uc_uid
              AND status = 'active'
            ORDER BY is_default DESC, added_at ASC
            LIMIT 1;

            IF v_existing_tenant IS NOT NULL THEN
                RETURN v_existing_tenant;
            END IF;

            -- KC sub IS a uuid; safe-cast, NULL if not castable. owner_user_id is
            -- NOT NULL, so a malformed (non-uuid) principal is correctly refused by
            -- the constraint rather than silently owning a workspace.
            BEGIN
                v_owner_uuid := p_uc_uid::uuid;
            EXCEPTION WHEN others THEN
                v_owner_uuid := NULL;
            END;

            -- (3) Build the slug source: username -> email local-part -> 'workspace'.
            -- Lowercase, fold every char outside [a-z0-9-] to '-', collapse runs of
            -- '-', and trim leading/trailing '-'. (No EXECUTE anywhere: the values
            -- only flow through regexp_replace/lower/||, so no SQL injection.)
            v_base := lower(coalesce(nullif(btrim(p_username), ''), ''));
            IF v_base = '' THEN
                -- email local-part fallback
                v_base := lower(coalesce(nullif(split_part(coalesce(p_email, ''), '@', 1), ''), ''));
            END IF;
            v_base := regexp_replace(v_base, '[^a-z0-9-]+', '-', 'g');
            v_base := regexp_replace(v_base, '-+', '-', 'g');
            v_base := btrim(v_base, '-');

            -- Leave room for the '-' + 6 hex suffix (7 chars) under the DB CHECK
            -- (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$', i.e. <= 63 chars), then re-trim
            -- so truncation can't leave a trailing '-'.
            v_base := btrim(left(v_base, 50), '-');
            IF v_base = '' THEN
                v_base := 'workspace';
            END IF;

            -- Display name mirrors create_tenant(): prefer username, else email,
            -- else the slug base.
            v_display_name := coalesce(
                nullif(btrim(p_username), ''),
                nullif(btrim(p_email), ''),
                v_base
            );

            -- Slug = base + '-' + 6 hex from a FRESH random uuid each attempt.
            -- Retry on the (astronomically unlikely) UNIQUE collision; bounded.
            LOOP
                v_attempt := v_attempt + 1;
                v_slug := v_base || '-' || substr(md5(gen_random_uuid()::text), 1, 6);

                BEGIN
                    INSERT INTO tenants (
                        slug,
                        kind,
                        display_name,
                        owner_user_id,
                        isolation_mode,
                        hipaa_mode,
                        qdrant_namespace,
                        garage_bucket_prefix,
                        graph_name
                    ) VALUES (
                        v_slug,
                        'personal',
                        v_display_name,
                        v_owner_uuid,
                        'standard',
                        false,
                        'contact_ops__' || v_slug,
                        'contact-ops-' || v_slug,
                        'contact_ops__' || replace(v_slug, '-', '_')
                    )
                    RETURNING id INTO v_new_tenant;

                    EXIT;  -- inserted cleanly
                EXCEPTION WHEN unique_violation THEN
                    IF v_attempt >= 5 THEN
                        RAISE;  -- give up after 5 tries; the suffix should never collide
                    END IF;
                    -- else loop and draw a fresh suffix
                END;
            END LOOP;

            -- (4) The admin self-membership the §4.4 gate enforces. Use the table
            -- defaults' shapes explicitly so onboarding intent is legible.
            INSERT INTO user_tenant_membership (
                user_uc_uid,
                tenant_id,
                role,
                status,
                is_default,
                consent_basis,
                added_by
            ) VALUES (
                p_uc_uid,
                v_new_tenant,
                'admin',
                'active',
                true,
                'self_provided',
                'onboarding:auto-provision'
            );

            -- (5)
            RETURN v_new_tenant;
        END
        $$
    """)
    op.execute(
        "GRANT EXECUTE ON FUNCTION provision_personal_workspace(text, text, text) "
        "TO contact_ops_app, contact_ops_ro, contact_ops_audit"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS provision_personal_workspace(text, text, text)"
    )
