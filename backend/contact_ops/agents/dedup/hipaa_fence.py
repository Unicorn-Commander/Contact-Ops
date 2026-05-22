from __future__ import annotations

from uuid import UUID


class PersonRef:
    """Lightweight person reference for HIPAA fence check."""
    __slots__ = ("id", "tenant_id")

    def __init__(self, id: UUID, tenant_id: UUID) -> None:
        self.id = id
        self.tenant_id = tenant_id


async def crosses_hipaa_fence(
    a: PersonRef,
    b: PersonRef,
    *,
    db_session=None,
) -> bool:
    """Returns True if merging a and b would violate the HIPAA boundary.

    Conservative: when in doubt, return True (block the merge).

    Rules:
    1. Same tenant -> safe (False = does NOT cross fence)
    2. Cross-tenant -> never allowed (True = crosses fence)
       This is an additional defense layer; RLS already prevents
       cross-tenant queries from returning data.
    """
    if a.tenant_id == b.tenant_id:
        return False
    return True


__all__ = [
    "PersonRef",
    "crosses_hipaa_fence",
]
