from __future__ import annotations

import pytest

from contact_ops.ops.schema_guard import (
    EXPECTED_ALEMBIC_REVISION,
    SchemaVersion,
    SchemaVersionMismatchError,
)


def test_expected_alembic_revision_tracks_head() -> None:
    assert EXPECTED_ALEMBIC_REVISION == "0039"


def test_schema_version_mismatch_is_explicit() -> None:
    version = SchemaVersion(expected="0039", actual="0038")
    assert not version.matches
    with pytest.raises(SchemaVersionMismatchError, match="expected 0039, got 0038"):
        raise SchemaVersionMismatchError(version)
