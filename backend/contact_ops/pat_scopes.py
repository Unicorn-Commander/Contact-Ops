"""Scope literals that a Contact-Ops personal access token may carry.

This is deliberately kept separate from the MCP registry so the frontend and
the token middleware have a small, import-safe backend catalogue to mirror.
The catalogue covers every scope declared by a registered MCP tool, plus the
category gate used by ``create_person`` and ``upsert_person``.
"""

from __future__ import annotations


PAT_SCOPE_CATALOGUE: tuple[str, ...] = (
    "acl:read",
    "acl:write",
    "address:geocode",
    "address:write",
    "carddav:admin",
    "carddav:read",
    "connectors:read",
    "connectors:write",
    "consent:write",
    "contactops:agents.admin",
    "contactops:inbox.review",
    "contactops:webhooks.admin",
    "contacts:read_personal",
    "dedup:apply",
    "dedup:propose",
    "dedup:read",
    "email:verify",
    "email:write",
    "identifier:read",
    "identifier:verify",
    "identifier:write",
    "media:read",
    "media:write",
    "membership:write",
    "name:read",
    "name:write",
    "notes:read",
    "notes:write",
    "org:archive",
    "org:read",
    "org:write",
    "pat:read",
    "pat:write",
    "person:archive",
    "person:bulk",
    "person:delete",
    "person:read",
    "person:write",
    "phone:normalize",
    "phone:write",
    "proposal:archive",
    "proposal:auto-apply",
    "proposal:read",
    "relationship:read",
    "relationship:write",
    "tag:bulk",
    "tag:read",
    "tag:write",
    "tenant:read",
    "tenant:write",
    "voice:match",
    "voice:read",
    "voice:write",
)

