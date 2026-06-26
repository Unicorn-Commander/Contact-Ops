"""Downstream effect application for approved / auto-applied proposals.

Phase 3 agents (Dedup, ...) emit ``action_event`` rows with ``status='proposed'``.
A human approving the proposal (``inbox_mutations.approve_proposal`` /
``bulk_approve``) or an agent auto-applying it at T2+ (``base.propose_action``)
flips that row to ``status='applied'``. Historically nothing then performed the
*real* effect: a ``dedup.propose_merge`` approval marked the row applied but never
merged the two person records, so the contacts stayed duplicated and the
calibration loop learned a positive outcome for a merge that never happened.

This module is the missing execution arm. It maps an applied proposal's
``event_type`` to the canonical, schema-correct primitive that realizes it:

* ``dedup.propose_merge`` -> ``data_quality.merge_people`` (reversible, audited).

The old ``agents/dedup/merge_executor`` was retired for targeting a phantom
schema (see ``mcp/tools/dedup_admin.py``); ``merge_people`` is the live path the
Data Quality UI already uses, so we reuse it verbatim through a system
``MCPContext`` rather than reimplementing the move-and-tombstone logic.

Cluster safety: dedup emits one proposal *per pairwise edge*, so a cluster of N
duplicates yields up to N*(N-1)/2 overlapping proposals. Approving them in any
order must converge on a single survivor without erroring on the second edge
(whose loser is already merged). ``_canonical_root`` follows ``merged_into_id``
so every edge resolves to current canonical persons; an edge whose endpoints
already share a root is a no-op success. This makes application idempotent and
order-independent.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from contact_ops.mcp.registry import MCPContext

_MERGE_EVENT_TYPE = "dedup.propose_merge"
# A non-UUID sentinel; emit_action_event coerces an unparseable human_authority
# to NULL, which is correct for an agent-initiated (no human) merge.
_AGENT_AUTHORITY = "agent"


async def _canonical_root(
    db: AsyncSession, person_id: uuid.UUID | str, *, _max_depth: int = 64
) -> uuid.UUID | None:
    """Follow ``merged_into_id`` to the live canonical person.

    Returns the canonical id, or ``None`` if the person row is missing. Guards
    against cycles / unbounded chains with a depth cap.
    """
    current = str(person_id)
    seen: set[str] = set()
    for _ in range(_max_depth):
        row = (
            await db.execute(
                text(
                    "SELECT merge_status::text AS st, merged_into_id "
                    "FROM persons WHERE id = CAST(:id AS uuid)"
                ),
                {"id": current},
            )
        ).first()
        if row is None:
            return None
        status, into = row[0], row[1]
        if status == "canonical" or into is None:
            return uuid.UUID(current)
        if current in seen:
            # Defensive: a cycle in the merge chain; stop at the current node.
            return uuid.UUID(current)
        seen.add(current)
        current = str(into)
    return uuid.UUID(current)


def _system_merge_ctx(
    *,
    app_db: AsyncSession,
    audit_db: AsyncSession,
    tenant_id: uuid.UUID,
    actor_chain: dict[str, Any],
    human_authority: str,
    request_id: str,
) -> MCPContext:
    """A STAFF / person:write context so ``merge_people`` runs for a non-human caller.

    ``require_role`` reads ``claims['realm_access']['roles']`` and
    ``require_scopes`` reads ``claims['scope']``; we grant exactly what
    ``merge_people`` asserts and nothing more.
    """
    # Imported at call time, not module load: importing contact_ops.mcp.registry
    # triggers the mcp package __init__ (registers every tool), which imports
    # inbox_mutations, which imports this module -> a circular import at load.
    from contact_ops.mcp.registry import MCPContext

    return MCPContext(
        tenant_id=tenant_id,
        user_id=str(actor_chain.get("sub", "system")),
        actor_chain=actor_chain,
        human_authority=human_authority,
        db=app_db,
        audit_db=audit_db,
        request_id=request_id,
        claims={
            "realm_access": {"roles": ["STAFF"]},
            "scope": "person:read person:write",
        },
    )


def _extract_merge_pair(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Pull (survivor_id, alias_id) from a dedup proposal payload.

    Prefers the explicit ``what_changes_if_merged`` survivor/alias the agent
    chose; falls back to ``candidate`` (person_a is survivor by convention).
    """
    if not isinstance(payload, dict):
        return None
    wc = payload.get("what_changes_if_merged")
    if isinstance(wc, dict) and wc.get("survivor_id") and wc.get("alias_id"):
        return str(wc["survivor_id"]), str(wc["alias_id"])
    cand = payload.get("candidate")
    if isinstance(cand, dict) and cand.get("person_a_id") and cand.get("person_b_id"):
        return str(cand["person_a_id"]), str(cand["person_b_id"])
    return None


async def execute_dedup_merge(
    *,
    app_db: AsyncSession,
    audit_db: AsyncSession,
    tenant_id: uuid.UUID,
    payload: dict[str, Any],
    confidence: float | None,
    actor_chain: dict[str, Any],
    human_authority: str,
    request_id: str,
) -> dict[str, Any]:
    """Realize one approved/auto-applied ``dedup.propose_merge`` proposal.

    Idempotent + cluster-safe. Returns a status dict; raises only on a genuine
    merge failure (so the caller's transaction rolls back and the proposal does
    not stay falsely 'applied').
    """
    pair = _extract_merge_pair(payload)
    if pair is None:
        return {"status": "skipped", "reason": "no_merge_pair_in_payload"}
    survivor_raw, alias_raw = pair

    surv_root = await _canonical_root(app_db, survivor_raw)
    alias_root = await _canonical_root(app_db, alias_raw)
    if surv_root is None or alias_root is None:
        return {"status": "skipped", "reason": "person_missing"}
    if surv_root == alias_root:
        # Another edge of the same cluster already unified these two.
        return {"status": "already_merged", "survivor_id": str(surv_root)}

    # Lazy import: data_quality pulls the full MCP tool surface; importing it at
    # module load would create an import cycle (inbox -> downstream -> data_quality
    # -> ...). Import at call time keeps this module light for the slim API image.
    from contact_ops.mcp.tools.data_quality import (
        MergePeopleInput,
        merge_people,
    )

    ctx = _system_merge_ctx(
        app_db=app_db,
        audit_db=audit_db,
        tenant_id=tenant_id,
        actor_chain=actor_chain,
        human_authority=human_authority,
        request_id=request_id,
    )
    out = await merge_people(
        ctx,
        MergePeopleInput(
            survivor_id=surv_root,
            loser_ids=[alias_root],
            dry_run=False,
            confidence=float(confidence) if confidence is not None else 1.0,
        ),
    )
    return {
        "status": "merged",
        "survivor_id": str(surv_root),
        "loser_id": str(alias_root),
        "merge_event_id": out.merge_event_id,
        "edges_repointed": out.edges_repointed,
    }


async def apply_downstream_effect(
    *,
    event_type: str,
    app_db: AsyncSession,
    audit_db: AsyncSession,
    tenant_id: uuid.UUID,
    payload: dict[str, Any] | None,
    confidence: float | None,
    actor_chain: dict[str, Any],
    human_authority: str,
    request_id: str,
) -> dict[str, Any] | None:
    """Dispatch an applied proposal to its real-world effect.

    Returns the effect result, or ``None`` when ``event_type`` has no registered
    downstream effect (the common case: person.create is handled inline, edge /
    tag / lifecycle proposals carry their effect in the action_event itself).
    """
    if event_type != _MERGE_EVENT_TYPE:
        return None
    return await execute_dedup_merge(
        app_db=app_db,
        audit_db=audit_db,
        tenant_id=tenant_id,
        payload=payload or {},
        confidence=confidence,
        actor_chain=actor_chain,
        human_authority=human_authority,
        request_id=request_id,
    )
