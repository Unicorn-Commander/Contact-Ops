"""media: media_assets, photos, voice_fingerprints, voice_samples

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # media_assets
    op.execute("""
        CREATE TABLE media_assets (
            id              uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id       uuid NOT NULL REFERENCES tenants(id),
            bucket          text NOT NULL,
            object_key      text NOT NULL,
            content_type    text NOT NULL,
            byte_size       bigint NOT NULL,
            sha256          bytea NOT NULL,
            width           integer,
            height          integer,
            duration_ms     integer,
            kind            text NOT NULL CHECK (kind IN ('photo','voice','video','business_card','vcard','document','evidence','logo','generic')),
            source_id       uuid,
            captured_at     timestamptz,
            exif            jsonb,
            retention_class retention_class NOT NULL DEFAULT 'operational_2y',
            legal_hold      boolean NOT NULL DEFAULT false,
            created_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (bucket, object_key)
        )
    """)
    op.execute("CREATE INDEX media_assets_tenant_idx ON media_assets(tenant_id)")
    op.execute("CREATE INDEX media_assets_sha256_idx ON media_assets(sha256)")

    # photos
    op.execute("""
        CREATE TABLE photos (
            id             uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            asset_id       uuid NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
            person_id      uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            is_primary     boolean NOT NULL DEFAULT false,
            source_id      uuid,
            observed_at    timestamptz NOT NULL DEFAULT now(),
            face_embedding vector(512),
            face_bbox      jsonb,
            quality_score  real,
            is_redacted    boolean NOT NULL DEFAULT false,
            created_at     timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE UNIQUE INDEX photos_primary_uniq ON photos(person_id) WHERE is_primary")
    op.execute("CREATE INDEX photos_person_idx ON photos(person_id)")
    op.execute("CREATE INDEX photos_face_hnsw ON photos USING hnsw (face_embedding vector_l2_ops)")

    # voice_fingerprints
    op.execute("""
        CREATE TABLE voice_fingerprints (
            id              uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id       uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            embedding       vector(256) NOT NULL,
            embedding_model text NOT NULL,
            sample_count    integer NOT NULL DEFAULT 0,
            total_duration_seconds numeric(10,2) NOT NULL DEFAULT 0,
            last_updated_at timestamptz NOT NULL DEFAULT now(),
            created_at      timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX voice_fp_person_idx ON voice_fingerprints(person_id)")
    op.execute("CREATE INDEX voice_fp_hnsw ON voice_fingerprints USING hnsw (embedding vector_cosine_ops)")
    op.execute("""
        ALTER TABLE persons ADD CONSTRAINT persons_voice_fp_fk
            FOREIGN KEY (voice_fingerprint_id) REFERENCES voice_fingerprints(id)
    """)

    # voice_samples
    op.execute("""
        CREATE TABLE voice_samples (
            id                     uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            asset_id               uuid NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
            person_id              uuid REFERENCES persons(id) ON DELETE SET NULL,
            voice_fingerprint_id   uuid REFERENCES voice_fingerprints(id),
            embedding              vector(256) NOT NULL,
            embedding_model        text NOT NULL,
            duration_seconds       numeric(10,2) NOT NULL,
            quality_score          real,
            snr_db                 real,
            captured_at            timestamptz NOT NULL,
            meeting_id             uuid,
            speaker_label          text,
            source_id              uuid,
            created_at             timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX voice_samples_person_idx ON voice_samples(person_id)")
    op.execute("CREATE INDEX voice_samples_fp_idx ON voice_samples(voice_fingerprint_id)")
    op.execute("CREATE INDEX voice_samples_meeting_idx ON voice_samples(meeting_id)")
    op.execute("CREATE INDEX voice_samples_hnsw ON voice_samples USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("ALTER TABLE persons DROP CONSTRAINT IF EXISTS persons_voice_fp_fk")
    op.execute("DROP TABLE IF EXISTS voice_samples")
    op.execute("DROP TABLE IF EXISTS voice_fingerprints")
    op.execute("DROP TABLE IF EXISTS photos")
    op.execute("DROP TABLE IF EXISTS media_assets")
