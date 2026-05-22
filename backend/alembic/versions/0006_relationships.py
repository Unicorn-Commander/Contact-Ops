"""relationships: person_org_role, person_person_relation, org_org_relation

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # person_org_role
    op.execute("""
        CREATE TABLE person_org_role (
            id                uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id         uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            organization_id   uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            role_type         role_type NOT NULL,
            title             text,
            department        text,
            seniority         seniority_level NOT NULL DEFAULT 'unknown',
            is_primary        boolean NOT NULL DEFAULT false,
            started_at        timestamptz,
            ended_at          timestamptz,
            employment_type   employment_type,
            ownership_percent numeric(6,3),
            equity_class      text,
            source_id         uuid,
            confidence        numeric(4,3) NOT NULL DEFAULT 1.000,
            observed_at       timestamptz NOT NULL DEFAULT now(),
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT por_date_ok CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
        )
    """)
    op.execute("CREATE INDEX por_person_idx ON person_org_role(person_id)")
    op.execute("CREATE INDEX por_org_idx ON person_org_role(organization_id)")
    op.execute("CREATE INDEX por_role_idx ON person_org_role(role_type)")
    op.execute("CREATE INDEX por_active_idx ON person_org_role(person_id, organization_id) WHERE ended_at IS NULL")
    op.execute("CREATE INDEX por_time_btree ON person_org_role(started_at, ended_at)")
    op.execute("""
        CREATE INDEX por_time_gist ON person_org_role USING gist (tstzrange(
            coalesce(started_at, '-infinity'::timestamptz),
            coalesce(ended_at, 'infinity'::timestamptz),
            '[)'
        ))
    """)
    op.execute("CREATE UNIQUE INDEX por_primary_uniq ON person_org_role(person_id) WHERE is_primary AND ended_at IS NULL")
    op.execute("""
        ALTER TABLE persons ADD CONSTRAINT persons_current_org_role_fk
            FOREIGN KEY (current_org_role_id) REFERENCES person_org_role(id) ON DELETE SET NULL
    """)

    # person_person_relation
    op.execute("""
        CREATE TABLE person_person_relation (
            id                          uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            from_person_id              uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            to_person_id                uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            relation_type               relation_type NOT NULL,
            inverse_relation_type       relation_type NOT NULL,
            strength                    real NOT NULL DEFAULT 0.5 CHECK (strength BETWEEN 0 AND 1),
            started_at                  date,
            ended_at                    date,
            context                     text,
            source_id                   uuid,
            confidence                  numeric(4,3) NOT NULL DEFAULT 1.000,
            observed_at                 timestamptz NOT NULL DEFAULT now(),
            is_bidirectional_confirmed  boolean NOT NULL DEFAULT false,
            tenant_visibility           uuid NOT NULL REFERENCES tenants(id),
            created_at                  timestamptz NOT NULL DEFAULT now(),
            updated_at                  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT no_self_relation CHECK (from_person_id <> to_person_id)
        )
    """)
    op.execute("CREATE INDEX ppr_from_idx ON person_person_relation(from_person_id, relation_type)")
    op.execute("CREATE INDEX ppr_to_idx ON person_person_relation(to_person_id, inverse_relation_type)")
    op.execute("CREATE INDEX ppr_tenant_idx ON person_person_relation(tenant_visibility)")
    op.execute("CREATE UNIQUE INDEX ppr_uniq ON person_person_relation(from_person_id, to_person_id, relation_type, tenant_visibility)")
    op.execute("CREATE INDEX ppr_strength_idx ON person_person_relation(from_person_id, strength DESC)")

    # org_org_relation
    op.execute("""
        CREATE TABLE org_org_relation (
            id              uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            from_org_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            to_org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            relation_type   org_relation_type NOT NULL,
            started_at      date,
            ended_at        date,
            source_id       uuid,
            confidence      numeric(4,3) NOT NULL DEFAULT 1.000,
            observed_at     timestamptz NOT NULL DEFAULT now(),
            tenant_visibility uuid NOT NULL REFERENCES tenants(id),
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT no_self_org_relation CHECK (from_org_id <> to_org_id)
        )
    """)
    op.execute("CREATE INDEX oor_from_idx ON org_org_relation(from_org_id, relation_type)")
    op.execute("CREATE INDEX oor_to_idx ON org_org_relation(to_org_id)")
    op.execute("CREATE UNIQUE INDEX oor_uniq ON org_org_relation(from_org_id, to_org_id, relation_type, tenant_visibility)")


def downgrade() -> None:
    op.execute("ALTER TABLE persons DROP CONSTRAINT IF EXISTS persons_current_org_role_fk")
    op.execute("DROP TABLE IF EXISTS org_org_relation")
    op.execute("DROP TABLE IF EXISTS person_person_relation")
    op.execute("DROP TABLE IF EXISTS person_org_role")
