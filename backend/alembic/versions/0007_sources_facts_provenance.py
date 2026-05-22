"""sources, facts, field_provenance

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # sources — extends catalogue_sources
    op.execute("""
        CREATE TABLE sources (
            id                      uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            legacy_source_id        uuid UNIQUE,
            source_type             source_type NOT NULL,
            source_uri              text,
            source_record_id        text,
            retrieved_at            timestamptz NOT NULL DEFAULT now(),
            retrieval_method        text,
            raw_payload_asset_id    uuid REFERENCES media_assets(id),
            source_reliability_base numeric(4,3) NOT NULL DEFAULT 0.7,
            tenant_id               uuid REFERENCES tenants(id),
            created_at              timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX sources_type_idx ON sources(source_type)")
    op.execute("CREATE INDEX sources_record_idx ON sources(source_record_id)")
    op.execute("CREATE INDEX sources_tenant_idx ON sources(tenant_id)")

    # facts
    op.execute("""
        CREATE TABLE facts (
            id                    uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id             uuid NOT NULL REFERENCES tenants(id),
            subject_kind          text NOT NULL CHECK (subject_kind IN ('person','organization')),
            subject_id            uuid NOT NULL,
            predicate             text NOT NULL,
            object_kind           text NOT NULL CHECK (object_kind IN ('literal','person','organization','address','url')),
            object_value          jsonb,
            object_ref_id         uuid,
            source_id             uuid NOT NULL REFERENCES sources(id),
            confidence            numeric(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            observed_at           timestamptz NOT NULL DEFAULT now(),
            valid_from            timestamptz,
            valid_until           timestamptz,
            superseded_by_fact_id uuid REFERENCES facts(id),
            human_verified        boolean NOT NULL DEFAULT false,
            verified_by_actor_id  uuid,
            verified_at           timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX facts_subject_idx ON facts(subject_kind, subject_id)")
    op.execute("CREATE INDEX facts_predicate_idx ON facts(predicate)")
    op.execute("CREATE INDEX facts_tenant_idx ON facts(tenant_id)")
    op.execute("CREATE INDEX facts_active_idx ON facts(subject_id, predicate) WHERE valid_until IS NULL AND superseded_by_fact_id IS NULL")
    op.execute("CREATE INDEX facts_object_gin ON facts USING gin (object_value jsonb_path_ops)")

    # field_provenance — FK to action_event added in migration 0010
    op.execute("""
        CREATE TABLE field_provenance (
            id                 uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            entity_type        text NOT NULL CHECK (entity_type IN ('person','organization')),
            entity_id          uuid NOT NULL,
            field_path         text NOT NULL,
            current_value      jsonb,
            set_by_event_id    uuid,
            set_by_actor       jsonb NOT NULL,
            source             source_type,
            source_record_id   text,
            confidence         numeric(4,3),
            established_at     timestamptz NOT NULL,
            last_verified_at   timestamptz,
            history            jsonb NOT NULL DEFAULT '[]',
            UNIQUE (entity_type, entity_id, field_path)
        )
    """)
    op.execute("CREATE INDEX fp_entity_idx ON field_provenance(entity_type, entity_id)")
    op.execute("CREATE INDEX fp_set_by_event_idx ON field_provenance(set_by_event_id)")
    op.execute("CREATE INDEX fp_low_confidence_idx ON field_provenance(entity_type, entity_id) WHERE confidence < 0.75")

    source_fk_tables = [
        "emails",
        "phones",
        "postal_addresses",
        "identifiers",
        "im_handles",
        "urls",
        "person_org_role",
        "person_person_relation",
        "org_org_relation",
        "voice_samples",
        "photos",
    ]
    for tbl in source_fk_tables:
        op.execute(f"""
            ALTER TABLE {tbl}
                ADD CONSTRAINT {tbl}_source_fk
                FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL
        """)


def downgrade() -> None:
    source_fk_tables = [
        "emails",
        "phones",
        "postal_addresses",
        "identifiers",
        "im_handles",
        "urls",
        "person_org_role",
        "person_person_relation",
        "org_org_relation",
        "voice_samples",
        "photos",
    ]
    for tbl in source_fk_tables:
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {tbl}_source_fk")
    op.execute("DROP TABLE IF EXISTS field_provenance")
    op.execute("DROP TABLE IF EXISTS facts")
    op.execute("DROP TABLE IF EXISTS sources")
