from __future__ import annotations

import pytest

from contact_ops.ops.schema_guard import (
    EXPECTED_ALEMBIC_REVISION,
    SchemaVersion,
    SchemaVersionMismatchError,
)


def test_expected_alembic_revision_tracks_head() -> None:
    # Derive the actual migration head so this guard never goes stale (it had
    # drifted to a hardcoded 0039 while the code shipped 0044). The prestart
    # schema assert bricks boot if EXPECTED_ALEMBIC_REVISION lags the head.
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()

    assert EXPECTED_ALEMBIC_REVISION == head


def test_schema_version_mismatch_is_explicit() -> None:
    version = SchemaVersion(expected="0039", actual="0038")
    assert not version.matches
    with pytest.raises(SchemaVersionMismatchError, match="expected 0039, got 0038"):
        raise SchemaVersionMismatchError(version)
