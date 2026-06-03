"""Canonical suite-role mapping (SUITE-IDENTITY §7).

The boundary translation between Contact-Ops' app-local membership role (the
lowercase `user_tenant_membership.role`: viewer|member|manager|admin) and the
canonical suite role set (viewer < member < manager < admin < owner). This is a
BOUNDARY translation only:

  * Brigade speaks canonical roles; an inbound canonical role is mapped to CO's
    app role via :func:`app_role` before CO decides its internal authority.
  * When CO REPORTS over MCP/federation it projects the canonical role via
    :func:`canonical_role` (SUITE-IDENTITY §7: "the canonical role is derived
    via this table when [the app] reports over MCP/federation").

It does NOT touch RLS and does NOT unify CO's two existing role vocabularies:

  * the uppercase `realm_access.roles` ladder used by `mcp/rbac.py:require_role`
    (CLIENT < STAFF < MANAGER < ADMIN) stays exactly as-is — it is internal
    authorization, not a suite boundary;
  * the lowercase membership CHECK constraint (`0035`: viewer|member|manager|
    admin) is unchanged.

CO has no `owner` rank, so `owner` is the inbound collapse to `admin` (CO's top,
SUITE-IDENTITY §7). Unknown roles fail to the least privilege (`viewer`) rather
than deny-by-crash, so a malformed federation role can never escalate and never
500s a report path.
"""

from __future__ import annotations

# CO membership role (lowercase, 0035) -> canonical suite role.
# CO's four ranks align 1:1 with the lower four canonical ranks.
_CO_TO_CANONICAL: dict[str, str] = {
    "viewer": "viewer",
    "member": "member",
    "manager": "manager",
    "admin": "admin",
}

# Canonical suite role -> CO membership role. `owner` collapses to `admin`
# (CO has no owner rank; admin is CO's top). Every canonical rank is mapped.
_CANONICAL_TO_CO: dict[str, str] = {
    "viewer": "viewer",
    "member": "member",
    "manager": "manager",
    "admin": "admin",
    "owner": "admin",
}

# The five canonical ranks, in ascending privilege order, for callers that need
# to compare/clamp. CO never emits `owner` (it has no such rank), but it accepts
# and collapses it on the inbound (Brigade) edge.
CANONICAL_ROLES: tuple[str, ...] = ("viewer", "member", "manager", "admin", "owner")

# Canonical suite role -> CO's UPPERCASE realm_access ladder role
# (mcp/rbac.py: CLIENT < STAFF < MANAGER < ADMIN). This is the inbound mapping
# the Brigade path uses: a Brigade token carries a CANONICAL role, and CO's
# internal authorization reads `realm_access.roles` (the uppercase ladder), so
# the canonical role is translated to that ladder before CO decides authority.
# `owner` collapses to `ADMIN` (CO's top, consistent with app_role).
_CANONICAL_TO_REALM_LADDER: dict[str, str] = {
    "viewer": "CLIENT",
    "member": "STAFF",
    "manager": "MANAGER",
    "admin": "ADMIN",
    "owner": "ADMIN",
}


def realm_role_from_canonical(suite_role: str | None) -> str | None:
    """Map a canonical suite role to CO's uppercase realm_access ladder role.

    Returns None when the input is not a recognized canonical role, so the
    Brigade path can fall back to its scope-derived role (`_role_from_scopes`)
    rather than silently demoting a token that carried a NON-canonical `role`
    value (e.g. an already-uppercase legacy role). `owner` -> `ADMIN`.
    """
    if not suite_role:
        return None
    return _CANONICAL_TO_REALM_LADDER.get(str(suite_role).strip().lower())


def canonical_role(co_role: str | None) -> str:
    """Map a CO membership role to the canonical suite role.

    Used wherever CO reports a role over MCP/federation. Unknown / None -> the
    least privilege (`viewer`).
    """
    if not co_role:
        return "viewer"
    return _CO_TO_CANONICAL.get(str(co_role).strip().lower(), "viewer")


def app_role(suite_role: str | None) -> str:
    """Map a canonical suite role to CO's membership role.

    Used on the inbound (Brigade) edge before CO decides its internal authority.
    `owner` collapses to `admin` (CO's top). Unknown / None -> `viewer` (least
    privilege), never a crash.
    """
    if not suite_role:
        return "viewer"
    return _CANONICAL_TO_CO.get(str(suite_role).strip().lower(), "viewer")
