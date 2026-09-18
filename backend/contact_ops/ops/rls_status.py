"""Process-local status for the boot RLS self-test."""

from __future__ import annotations

from typing import Any

_status: dict[str, Any] = {"status": "not_run"}


def set_rls_status(status: str, **details: Any) -> None:
    _status.clear()
    _status.update(status=status, **details)


def get_rls_status() -> dict[str, Any]:
    return dict(_status)
