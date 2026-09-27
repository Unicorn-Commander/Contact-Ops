"""Process-local status for the boot RLS self-test."""

from __future__ import annotations

from typing import Any

_status: dict[str, Any] = {"status": "not_run"}


class RLSSelfTestError(RuntimeError):
    """Raised when boot cannot prove tenant isolation."""


def evaluate_rls_isolation(
    *, privileged: bool, probe_counts: tuple[int, int], cross_counts: tuple[int, int]
) -> str:
    """Evaluate completed observations without needing a live database."""
    if any(count == 0 for count in probe_counts):
        raise RLSSelfTestError("RLS self-test probe tenant has no visible persons")
    if privileged:
        return "inert_privileged"
    if any(count != 0 for count in cross_counts):
        raise RLSSelfTestError(
            "RLS self-test failed: cross-tenant person count is non-zero "
            "under a non-privileged role"
        )
    return "passed"


def set_rls_status(status: str, **details: Any) -> None:
    _status.clear()
    _status.update(status=status, **details)


def get_rls_status() -> dict[str, Any]:
    return dict(_status)
