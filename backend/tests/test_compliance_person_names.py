"""Regression guards for person_names compliance wiring (no DB needed).

The DB-backed purge/export flows are covered by test_compliance_e2e.py (Docker);
these assertions lock the static wiring so a future table addition can't
silently drop person_names from the erase cascade.
"""

from __future__ import annotations

from contact_ops.services.compliance_erasure import _CASCADE_CHILD_TABLES


def test_person_names_in_erase_cascade_tables() -> None:
    assert ("person_names", "person_id") in _CASCADE_CHILD_TABLES


def test_erase_cascade_has_no_duplicate_tables() -> None:
    tables = [table for table, _ in _CASCADE_CHILD_TABLES]
    assert len(tables) == len(set(tables))
