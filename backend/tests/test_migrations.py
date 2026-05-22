"""
Migration roundtrip tests.

Uses the conftest postgres_container fixture (which already applies
migrations).  Verifies:
  - upgrade head is idempotent (second run is a no-op)
  - downgrade base drops everything cleanly
  - full roundtrip (head → base → head) succeeds
  - key schema artifacts exist afterwards
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


TABLES_THAT_MUST_EXIST: set[str] = {
    "tenants", "persons", "organizations", "emails", "phones",
    "postal_addresses", "identifiers", "im_handles", "urls",
    "action_event", "merge_history", "person_alias", "organization_alias",
    "agent_registry", "sources", "facts", "field_provenance",
    "consent_records", "person_tenant_membership",
    "organization_tenant_membership",
}

RLS_TABLES: set[str] = {
    "persons", "organizations", "emails", "phones", "postal_addresses",
    "identifiers", "im_handles", "urls", "action_event", "merge_history",
    "person_tenant_membership", "organization_tenant_membership",
}

EXPECTED_TRIGGERS: set[str] = {
    "trg_enforce_hipaa_merge",
    "trg_rewrite_alias_person_id",
    "trg_rewrite_alias_person_id_phones",
    "trg_rewrite_alias_person_id_addr",
    "trg_rewrite_alias_person_id_id",
    "trg_rewrite_alias_person_id_im",
}


def _sync_url(asyncpg_url: str) -> str:
    return asyncpg_url.replace("postgresql+asyncpg://", "postgresql://")


def _make_config(sync_url: str) -> Config:
    alemsrc = Path(__file__).resolve().parent.parent / "alembic"
    cfg = Config(str(alemsrc.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(alemsrc))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


@pytest.fixture
def sync_engine(postgres_container: str):
    """Sync engine for Alembic commands (avoids asyncio.run conflicts)."""
    import os as _os

    sync_url = postgres_container.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    _os.environ["MIGRATION_DATABASE_URL"] = postgres_container
    eng = create_engine(sync_url)
    yield eng
    eng.dispose()


def test_migration_idempotent(sync_engine):
    """Running upgrade head twice is a no-op (migrations already applied by conftest)."""
    with sync_engine.connect() as conn:
        r = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
        )
        tables = {row[0] for row in r.fetchall()}
    missing = TABLES_THAT_MUST_EXIST - tables
    assert not missing, f"Tables missing after initial migration: {missing}"


def test_downgrade_base(postgres_container):
    """Downgrade to base drops everything cleanly."""
    import os as _os

    sync_url = postgres_container.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    _os.environ["MIGRATION_DATABASE_URL"] = postgres_container
    cfg = _make_config(sync_url)

    command.downgrade(cfg, "base")

    eng = create_engine(sync_url)
    with eng.connect() as conn:
        r = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        remaining = {row[0] for row in r.fetchall()}
    remaining.discard("alembic_version")
    assert not remaining, f"Tables left after downgrade: {remaining}"
    eng.dispose()


def test_roundtrip(postgres_container):
    """Full roundtrip: base → head → base → head."""
    import os as _os

    sync_url = postgres_container.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    _os.environ["MIGRATION_DATABASE_URL"] = postgres_container
    cfg = _make_config(sync_url)

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    eng = create_engine(sync_url)
    with eng.connect() as conn:
        r = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
        )
        tables = {row[0] for row in r.fetchall()}
    missing = TABLES_THAT_MUST_EXIST - tables
    assert not missing, f"Tables missing after roundtrip: {missing}"
    eng.dispose()


def test_rls_enabled(sync_engine):
    """RLS is enabled on the critical tables."""
    with sync_engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT relname FROM pg_class "
                "JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
                "WHERE nspname = 'public' AND relrowsecurity = true"
            )
        )
        rls_enabled = {row[0] for row in result.fetchall()}
    missing = RLS_TABLES - rls_enabled
    assert not missing, f"RLS missing on tables: {missing}"


def test_triggers_exist(sync_engine):
    """Key triggers are installed."""
    with sync_engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT trigger_name FROM information_schema.triggers "
                "WHERE trigger_schema = 'public'"
            )
        )
        triggers = {row[0] for row in result.fetchall()}
    missing = EXPECTED_TRIGGERS - triggers
    assert not missing, f"Missing triggers: {missing}"


def test_roles_exist(sync_engine):
    """Database roles contact_ops_app and contact_ops_audit exist."""
    with sync_engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT rolname FROM pg_roles "
                "WHERE rolname IN ('contact_ops_app', 'contact_ops_audit') "
                "ORDER BY rolname"
            )
        )
        roles = {row[0] for row in result.fetchall()}
    assert "contact_ops_app" in roles
    assert "contact_ops_audit" in roles
