"""Agent suppression rules — backs ``reject_proposal(mode="mute")``.

A suppression rule says "don't show me proposals from <agent_slug> about
<aggregate_id>'s <field_name> field until <expires_at>." Patterns:

* Full mute: aggregate_id NULL, field_name NULL — silence this agent for
  this whole tenant
* Per-entity: aggregate_id set, field_name NULL — silence this agent for
  one entity, all fields
* Per-field: aggregate_id set, field_name set — the common case from the
  Mute hotkey (`m`)
* Per-field across all entities: aggregate_id NULL, field_name set —
  rare; used when an agent is wrong about a field type in general

Expires_at default = +90 days. Rule cleanup is incidental — old rows are
kept for audit but ignored by ``is_suppressed``.
"""
# ruff: noqa: S608
# S608 OK: list_suppression_rules composes its WHERE from a closed
# allowlist of fragments; all values go through bind params.

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_suppression_rule(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agent_slug: str,
    aggregate_type: str | None,
    aggregate_id: uuid.UUID | None,
    field_name: str | None,
    created_by: uuid.UUID,
    expires_at: datetime | None = None,
    note: str | None = None,
) -> uuid.UUID:
    """Insert a suppression rule. Returns the new rule's UUID.

    ``expires_at=None`` -> default +90 days. Caller must supply at least
    one of (aggregate_id, field_name) — fully-blank "silence agent in
    this tenant entirely" requires an explicit choice.
    """
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(days=90)

    result = await db.execute(
        text(
            """
            INSERT INTO agent_suppression_rules (
                tenant_id, agent_slug, aggregate_type, aggregate_id,
                field_name, expires_at, created_by, note
            ) VALUES (
                CAST(:tenant AS uuid), :agent_slug, :aggregate_type,
                CAST(:aggregate_id AS uuid), :field_name, :expires_at,
                CAST(:created_by AS uuid), :note
            )
            RETURNING id
            """
        ),
        {
            "tenant": str(tenant_id),
            "agent_slug": agent_slug,
            "aggregate_type": aggregate_type,
            "aggregate_id": str(aggregate_id) if aggregate_id else None,
            "field_name": field_name,
            "expires_at": expires_at,
            "created_by": str(created_by),
            "note": (note or "")[:2000] or None,
        },
    )
    return uuid.UUID(str(result.scalar_one()))


async def is_suppressed(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agent_slug: str,
    aggregate_id: uuid.UUID,
    field_names: list[str] | None,
) -> bool:
    """True if any active rule covers (agent_slug, aggregate_id, field_names).

    A rule covers a proposal when:
        agent_slug matches
        AND (rule.aggregate_id IS NULL OR rule.aggregate_id = aggregate_id)
        AND (rule.field_name IS NULL OR rule.field_name = ANY(field_names))
        AND expires_at > now()
    """
    field_list = field_names or []
    result = await db.execute(
        text(
            """
            SELECT 1
            FROM agent_suppression_rules
            WHERE tenant_id = CAST(:tenant AS uuid)
              AND agent_slug = :agent_slug
              AND (aggregate_id IS NULL OR aggregate_id = CAST(:aggregate_id AS uuid))
              AND (
                  field_name IS NULL
                  OR field_name = ANY(CAST(:fields AS text[]))
              )
              AND expires_at > now()
            LIMIT 1
            """
        ),
        {
            "tenant": str(tenant_id),
            "agent_slug": agent_slug,
            "aggregate_id": str(aggregate_id),
            "fields": field_list,
        },
    )
    return result.scalar_one_or_none() is not None


async def list_suppression_rules(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agent_slug: str | None = None,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    """List rules for admin / audit view."""
    where = ["tenant_id = CAST(:tenant AS uuid)"]
    params: dict[str, Any] = {"tenant": str(tenant_id)}
    if agent_slug is not None:
        where.append("agent_slug = :agent_slug")
        params["agent_slug"] = agent_slug
    if not include_expired:
        where.append("expires_at > now()")
    query = (
        "SELECT id, tenant_id, agent_slug, aggregate_type, aggregate_id, "
        "field_name, expires_at, created_by, created_at, note "
        "FROM agent_suppression_rules "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY created_at DESC"
    )
    result = await db.execute(text(query), params)
    return [dict(row) for row in result.mappings()]


async def delete_suppression_rule(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rule_id: uuid.UUID,
) -> bool:
    """Hard-delete a rule. Returns True if a row was removed."""
    result = await db.execute(
        text(
            """
            DELETE FROM agent_suppression_rules
            WHERE id = CAST(:id AS uuid)
              AND tenant_id = CAST(:tenant AS uuid)
            """
        ),
        {"id": str(rule_id), "tenant": str(tenant_id)},
    )
    rowcount = getattr(result, "rowcount", 0) or 0
    return int(rowcount) > 0


__all__ = [
    "create_suppression_rule",
    "delete_suppression_rule",
    "is_suppressed",
    "list_suppression_rules",
]
