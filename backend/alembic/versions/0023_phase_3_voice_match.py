"""phase 3.2 voice match: voice_samples + voice_consent + voice_fingerprints columns + Qdrant bootstrap

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-22

(Renumbered from 0022 -> 0023 during the Phase 3.1/3.2 untangle.
Dedup's migration 0022 lands first; this one applies on top.)


Covers Phase 3 Design §3.8, §3.9, and §10 in full. Specifically:

* Extends ``voice_fingerprints`` with adaptive-threshold machinery
  (intra_variance, auto_link_threshold, propose_threshold,
   embedding_model_version, language_primary, last_recompute_at,
   centroid_decayed_at, source_meeting_ids).
* Creates ``voice_consent`` table (GDPR Art. 9 + BIPA-compliant
  voice biometric opt-in record per §3.8).
* Installs RLS policies on ``voice_consent`` and extends the existing
  policy on ``voice_fingerprints``.
* Bootstraps the ``contact_ops_person_voice`` Qdrant collection with
  proper HNSW config (m=0, payload_m=16) and all payload indexes,
  idempotently (if Qdrant unreachable, log warning and continue).

Downgrade is implemented but should be exercised only in test.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

logger = logging.getLogger(__name__)

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _extend_voice_fingerprints()
    _create_voice_consent()
    _install_rls_policies()
    _bootstrap_qdrant_collection()


def downgrade() -> None:
    _drop_qdrant_collection()
    _remove_rls_policies()
    op.execute("DROP TABLE IF EXISTS voice_consent")
    op.execute(
        "ALTER TABLE voice_fingerprints DROP COLUMN IF EXISTS source_meeting_ids"
    )
    op.execute(
        "ALTER TABLE voice_fingerprints DROP COLUMN IF EXISTS centroid_decayed_at"
    )
    op.execute(
        "ALTER TABLE voice_fingerprints DROP COLUMN IF EXISTS last_recompute_at"
    )
    op.execute(
        "ALTER TABLE voice_fingerprints DROP COLUMN IF EXISTS language_primary"
    )
    op.execute(
        "ALTER TABLE voice_fingerprints DROP COLUMN IF EXISTS embedding_model_version"
    )
    op.execute(
        "ALTER TABLE voice_fingerprints DROP COLUMN IF EXISTS propose_threshold"
    )
    op.execute(
        "ALTER TABLE voice_fingerprints DROP COLUMN IF EXISTS auto_link_threshold"
    )
    op.execute(
        "ALTER TABLE voice_fingerprints DROP COLUMN IF EXISTS intra_variance"
    )


# ---- voice_fingerprints extensions ----

def _extend_voice_fingerprints() -> None:
    """Phase 3 Design §3.9: adaptive-threshold machinery."""
    op.execute(
        """
        ALTER TABLE voice_fingerprints
            ADD COLUMN intra_variance DOUBLE PRECISION,
            ADD COLUMN auto_link_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.78,
            ADD COLUMN propose_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.62,
            ADD COLUMN embedding_model_version TEXT NOT NULL DEFAULT 'wespeaker-resnet34-LM-2024.03',
            ADD COLUMN language_primary TEXT,
            ADD COLUMN last_recompute_at TIMESTAMPTZ,
            ADD COLUMN centroid_decayed_at TIMESTAMPTZ,
            ADD COLUMN source_meeting_ids UUID[]
        """
    )
    op.execute(
        "COMMENT ON COLUMN voice_fingerprints.intra_variance IS "
        "'Mean (1 - cosine(emb_i, centroid)) across the rolling N=8 samples. "
        "Used to compute per-person adaptive thresholds.'"
    )
    op.execute(
        "COMMENT ON COLUMN voice_fingerprints.auto_link_threshold IS "
        "'Cosine threshold for auto-link. Default 0.78 per §10.5. "
        "After sample_count >= 5, switches to per-person value.'"
    )
    op.execute(
        "COMMENT ON COLUMN voice_fingerprints.propose_threshold IS "
        "'Cosine threshold for proposing a link. Default 0.62 per §10.5.'"
    )
    op.execute(
        "COMMENT ON COLUMN voice_fingerprints.language_primary IS "
        "'BCP-47 language tag for multi-lingual centroids (§10.8). "
        "NULL means language-agnostic.'"
    )


# ---- voice_consent table ----

def _create_voice_consent() -> None:
    """Phase 3 Design §3.8: GDPR Art. 9 + BIPA opt-in record."""
    op.execute(
        """
        CREATE TABLE voice_consent (
            id                      UUID PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id               UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            consent_granted_at      TIMESTAMPTZ NOT NULL,
            consent_revoked_at      TIMESTAMPTZ,
            consent_text_version    TEXT NOT NULL,
            consent_method          TEXT NOT NULL,
            consent_ip              INET,
            granted_by              UUID,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (person_id, tenant_id, consent_granted_at)
        )
        """
    )
    op.execute(
        "CREATE INDEX voice_consent_active_idx "
        "ON voice_consent (person_id, tenant_id) WHERE consent_revoked_at IS NULL"
    )
    op.execute(
        "COMMENT ON TABLE voice_consent IS "
        "'GDPR Article 9 + BIPA-compliant opt-in record for voice biometrics. "
        "The Voice Match Agent refuses to extract embeddings without an active row.'"
    )
    op.execute(
        "COMMENT ON COLUMN voice_consent.consent_method IS "
        "'web_optin | api | email_confirmation | voice_recording_intro'"
    )
    op.execute(
        "COMMENT ON COLUMN voice_consent.consent_text_version IS "
        "'Semantic version of the consent text the user saw. Re-prompt when this changes.'"
    )


# ---- RLS policies ----

def _install_rls_policies() -> None:
    """Tenant isolation via ``app.tenant_id`` GUC.

    Follows the same pattern as migration 0021's ``_install_rls_policies``.
    """
    # RLS on voice_consent
    op.execute("ALTER TABLE voice_consent ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE voice_consent FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON voice_consent
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON voice_consent TO contact_ops_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON voice_consent TO contact_ops_audit")

    # Ensure RLS is on voice_fingerprints (may already be from Phase 2
    # migration 0015, but we force it to be safe).
    op.execute("ALTER TABLE voice_fingerprints ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE voice_fingerprints FORCE ROW LEVEL SECURITY")
    # Postgres doesn't support `CREATE POLICY IF NOT EXISTS`; drop-then-create
    # is the idiomatic way to be idempotent. The Foundation policies on
    # voice_fingerprints predate this migration so we explicitly replace.
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON voice_fingerprints"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON voice_fingerprints
            USING (person_id IN (
                SELECT id FROM persons WHERE canonical_owner_tenant_id = current_tenant_id()
            ))
            WITH CHECK (person_id IN (
                SELECT id FROM persons WHERE canonical_owner_tenant_id = current_tenant_id()
            ))
        """
    )


# ---- Qdrant collection bootstrap ----

def _bootstrap_qdrant_collection() -> None:
    """Idempotent Qdrant collection bootstrap.

    If Qdrant is unreachable (e.g., unit test environment), log a warning
    and continue. The collection will be created on first use by the
    ``QdrantVoiceBackendImpl._ensure_collection`` path.
    """
    import os

    qdrant_url = os.environ.get("QDRANT_URL", "")
    qdrant_api_key = os.environ.get("QDRANT_API_KEY", "")

    if not qdrant_url or os.environ.get("ENV") == "test":
        logger.info("Qdrant not configured; skipping collection bootstrap")
        op.execute(
            "SELECT 1 -- Qdrant bootstrap skipped (ENV=test or QDRANT_URL unset)"
        )
        return

    try:
        from qdrant_client.http import models as qm
        from qdrant_client import QdrantClient

        client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key or None)
        existing = client.get_collections()
        names = {c.name for c in existing.collections}
        collection_name = "contact_ops_person_voice"

        if collection_name not in names:
            client.create_collection(
                collection_name=collection_name,
                vectors_config=qm.VectorParams(
                    size=256,
                    distance=qm.Distance.COSINE,
                ),
                hnsw_config=qm.HnswConfigDiff(
                    m=0,
                    payload_m=16,
                ),
                on_disk_payload=True,
            )
            client.create_payload_index(
                collection_name=collection_name,
                field_name="tenant_id",
                field_schema=qm.KeywordIndexParams(
                    type="keyword", is_tenant=True,
                ),
            )
            client.create_payload_index(
                collection_name=collection_name,
                field_name="hipaa_scope",
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
            client.create_payload_index(
                collection_name=collection_name,
                field_name="consent_active",
                field_schema=qm.PayloadSchemaType.BOOL,
            )
            client.create_payload_index(
                collection_name=collection_name,
                field_name="person_id",
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
            client.create_payload_index(
                collection_name=collection_name,
                field_name="language_primary",
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
            logger.info("Qdrant collection %s bootstrapped", collection_name)
        else:
            logger.info("Qdrant collection %s already exists", collection_name)
    except Exception:
        logger.warning(
            "Qdrant collection bootstrap failed; will be created on first use",
            exc_info=True,
        )
        op.execute(
            "SELECT 1 -- Qdrant bootstrap attempted but failed; will retry at runtime"
        )


def _drop_qdrant_collection() -> None:
    """Drop the Qdrant collection on downgrade (best-effort)."""
    import os

    qdrant_url = os.environ.get("QDRANT_URL", "")
    if not qdrant_url or os.environ.get("ENV") == "test":
        return
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=qdrant_url)
        client.delete_collection(collection_name="contact_ops_person_voice")
        logger.info("Qdrant collection contact_ops_person_voice dropped")
    except Exception:
        logger.warning("Qdrant collection drop failed; manual cleanup may be needed", exc_info=True)


def _remove_rls_policies() -> None:
    """Remove RLS policies added by this migration."""
    op.execute("ALTER TABLE voice_consent NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE voice_consent DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON voice_consent")
