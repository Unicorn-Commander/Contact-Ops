"""
Migration roundtrip tests.

Verifies that all 15 Alembic migrations can be applied and rolled back
without errors.  This is the core Phase 0 gate: if migrations fail,
the build fails.
"""

import pytest
from alembic import command, config


@pytest.fixture(scope="module")
def alembic_cfg(tmp_path_factory):
    """Return an Alembic Config pointing at a test database."""
    cfg = config.Config("backend/alembic.ini")
    return cfg


def test_migration_upgrade_head(alembic_cfg):
    """Apply all migrations up to head."""
    command.upgrade(alembic_cfg, "head")


def test_migration_downgrade_base(alembic_cfg):
    """Roll back all migrations to base."""
    command.downgrade(alembic_cfg, "base")


def test_migration_roundtrip(alembic_cfg):
    """Full roundtrip: upgrade → downgrade → upgrade."""
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
