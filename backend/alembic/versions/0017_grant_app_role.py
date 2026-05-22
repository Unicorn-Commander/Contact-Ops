"""grant app and read-only roles

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO contact_ops_app, contact_ops_ro, contact_ops_audit")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO contact_ops_app")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO contact_ops_ro")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO contact_ops_audit")
    op.execute("REVOKE UPDATE, DELETE ON action_event FROM contact_ops_app")
    op.execute("GRANT INSERT, SELECT ON action_event TO contact_ops_audit")

    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO contact_ops_app")
    op.execute("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO contact_ops_ro")
    op.execute("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO contact_ops_audit")

    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO contact_ops_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT ON TABLES TO contact_ops_ro
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT ON TABLES TO contact_ops_audit
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO contact_ops_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT ON SEQUENCES TO contact_ops_ro
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT ON SEQUENCES TO contact_ops_audit
    """)


def downgrade() -> None:
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE SELECT ON SEQUENCES FROM contact_ops_audit
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE SELECT ON SEQUENCES FROM contact_ops_ro
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE USAGE, SELECT ON SEQUENCES FROM contact_ops_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE SELECT ON TABLES FROM contact_ops_audit
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE SELECT ON TABLES FROM contact_ops_ro
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM contact_ops_app
    """)

    op.execute("REVOKE SELECT ON ALL SEQUENCES IN SCHEMA public FROM contact_ops_audit")
    op.execute("REVOKE SELECT ON ALL SEQUENCES IN SCHEMA public FROM contact_ops_ro")
    op.execute("REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM contact_ops_app")
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM contact_ops_audit")
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM contact_ops_ro")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM contact_ops_app")

    op.execute("GRANT INSERT, SELECT ON action_event TO contact_ops_app")
    op.execute("GRANT INSERT, SELECT ON action_event TO contact_ops_audit")
    op.execute("REVOKE UPDATE, DELETE ON action_event FROM contact_ops_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM contact_ops_app, contact_ops_ro, contact_ops_audit")
