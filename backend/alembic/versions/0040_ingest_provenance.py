"""ingest provenance: source + consent + rollup columns on persons (P-00059)

Revision ID: 0040
Revises: 0039
Create Date: 2026-06-05

Additive, behavioral NO-OP at deploy. Adds the lightweight ingest-provenance +
rollup columns the CONTACT-OPS-INGEST-CONTRACT (§3) needs so leadgen apps can
push graduated leads through `POST /api/v1/contacts/ingest` and the shared
`contactops_push` connector, recording WHERE a person came from without copying
the producing app's vertical data. All columns are NULLable / default NULL, so
existing rows and every non-ingest code path are byte-for-byte unchanged.

Columns on persons:
  * source_label  text  — which producer (e.g. 'NorthwindLeads'); free text.
  * source_kind   text  — coarse producer category (e.g. 'leadgen_app','agent',
                          'manual','import'); app-defined, left unconstrained.
  * external_ref  text  — the producer's own id for this person (back-reference +
                          a dedup fallback when a lead lacks email/phone).
  * consent_basis text  — CHECK against the ConsentBasis vocabulary; the legal
                          provenance the contract REQUIRES on ingest (enforced at
                          the API layer; NULLable here for back-compat with the
                          many persons created via non-ingest paths).
  * lead_score    numeric(6,2) — optional denormalized rollup; the producing app
                          stays the source of truth.
  * stage         text  — optional denormalized rollup (pipeline stage);
                          app-defined.

Idempotency: the ingest upsert primarily dedups on email/phone (the existing
core). external_ref is stored + indexed (NON-unique, partial) as a secondary
match key for leads that arrive without email/phone. A per-(source,person) ref
table with a UNIQUE (tenant, source_label, external_ref) constraint — to support
one person sourced by multiple apps with per-source rollups — is the documented
v1.1 hardening; this v1 records the primary/latest source denormalized on the
person, sufficient to unblock the per-app integrations.

TEXT (+ CHECK only on consent_basis) per the suite convention (0034/0039): never
a Postgres ENUM, so the vocabulary stays a cheap CHECK change. Table-level grants
already cover new columns; persons RLS is unchanged (row-level). Additive +
reversible: down() drops the index + columns. Single alembic head: down_revision
is 0039, the sole child.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mirrors contact_ops.models.enums.ConsentBasis — kept inline so the migration is
# self-contained (migrations must not import app code that may drift).
_CONSENT_VALUES = (
    "self_provided",
    "business_card",
    "opted_in",
    "public_record",
    "legitimate_interest",
    "vendor_provided",
)


def upgrade() -> None:
    consent_check = ", ".join(f"'{v}'" for v in _CONSENT_VALUES)
    op.execute(
        f"""
        ALTER TABLE persons
            ADD COLUMN source_label  text,
            ADD COLUMN source_kind   text,
            ADD COLUMN external_ref  text,
            ADD COLUMN consent_basis text CHECK (consent_basis IN ({consent_check})),
            ADD COLUMN lead_score    numeric(6,2),
            ADD COLUMN stage         text
        """
    )
    op.execute(
        "COMMENT ON COLUMN persons.source_label IS "
        "'Ingest provenance (CONTACT-OPS-INGEST-CONTRACT §3): producing app, e.g. NorthwindLeads.'"
    )
    op.execute(
        "COMMENT ON COLUMN persons.external_ref IS "
        "'Producer''s own id for this person; back-reference + secondary dedup key.'"
    )
    op.execute(
        "COMMENT ON COLUMN persons.consent_basis IS "
        "'Legal provenance (ConsentBasis vocab); REQUIRED on ingest, enforced at the API layer.'"
    )
    # Secondary (NON-unique, partial) match key for leads arriving without
    # email/phone. NOT unique by design — the per-source ref table is the v1.1
    # hardening; a unique constraint here would wrongly collide a single person
    # sourced by two apps that happen to reuse an external_ref string.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_persons_source_external_ref
            ON persons (canonical_owner_tenant_id, source_label, external_ref)
            WHERE external_ref IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_persons_source_external_ref")
    op.execute(
        """
        ALTER TABLE persons
            DROP COLUMN IF EXISTS stage,
            DROP COLUMN IF EXISTS lead_score,
            DROP COLUMN IF EXISTS consent_basis,
            DROP COLUMN IF EXISTS external_ref,
            DROP COLUMN IF EXISTS source_kind,
            DROP COLUMN IF EXISTS source_label
        """
    )
