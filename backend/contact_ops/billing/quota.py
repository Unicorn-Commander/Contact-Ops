"""Numeric quota gate — layers plan ALLOWANCES onto the entitlement gate.

Where ``contact_ops.mcp.entitlement`` answers *"does this tenant's plan unlock
this tool at all?"* (a tier-ladder yes/no), this module answers the follow-on
*"has this tenant exhausted the plan's allowance for it?"*. It is the second of
the two monetization gates and deliberately mirrors ``check_entitlement``'s
structure bit-for-bit so the two read as one policy:

  * self-host bypass first — ``DEPLOYMENT_MODE == 'self_host'`` returns None
    (their cell, their data; never metered, never capped);
  * free tools short-circuit with ZERO DB I/O — a tool whose ``tier_for_tool``
    is the free ``DEFAULT_TIER`` is never metered, so it is never quota-checked;
  * only the metered (pro/enterprise) tools — exactly the GPU/agent ``_PRO_TOOLS``
    by the "gate on compute" line — resolve a plan and check an allowance;
  * SHADOW-FIRST: with ``settings.BILLING_QUOTA_ENFORCED`` False (default) an
    over-limit tenant is LOGGED (``quota_would_deny``) and ALLOWED. Flip the flag
    to actually return a ``QUOTA_EXCEEDED`` ToolError. This mirrors the
    ``ENTITLEMENT_ENFORCED`` / ``MEMBERSHIP_GATE_ENFORCED`` rollout caution — a
    mis-set cap would otherwise hard-stop a paying tenant's expected work.

v1 ENFORCEMENT SCOPE / LIMITATION (read before relying on this):

  Two allowances live in a ``PlanSpec``: ``max_contacts`` and
  ``monthly_agent_runs``. Only ONE is enforced locally in v1:

    * ``max_contacts`` — LOCALLY COUNTABLE with zero new schema: the tenant's
      contacts are rows in ``person_tenant_membership`` (one per contact in the
      workspace), readable under the SAME RLS-bound ``db`` the gate is already
      using. We COUNT(*) that table (RLS scopes it to the caller's tenant — the
      query can never see another tenant's rows) and compare to the cap. This is
      the cap a metered write would push a tenant over, so it is the right thing
      to enforce at the door of a pro tool.

    * ``monthly_agent_runs`` — NOT locally enforced in v1. CO has no local usage
      / metering table; agent-run usage is emitted BEST-EFFORT to suite Lago
      (metric ``contact_ops_agent_run``) and the authoritative monthly count
      lives there. Enforcing it locally would mean either (a) a per-event Lago
      round-trip on every tool call — fragile, slow, and a hiccup must never 500
      a tool — or (b) a new local ledger table + migration, which v1 explicitly
      avoids. So ``monthly_agent_runs`` is treated as METERED-TO-LAGO-ONLY:
      recorded for billing, surfaced as a plan attribute, but not gated here yet.
      When a local usage ledger lands (or a cached Lago usage read is added),
      extend ``check_quota`` to consult it; the shadow-first / self-host / free
      short-circuit scaffolding below already covers that path.

  Net v1 behaviour: a pro tool is allowed unless the tenant is already over its
  ``max_contacts`` cap (and only then blocks when ENFORCED is on). Free tools and
  self-host are never touched. The agent-run allowance rides on Lago metering.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.billing.catalog import PlanSpec, plan_for_tier
from contact_ops.core.config import Settings
from contact_ops.mcp.entitlement import (
    DEFAULT_TIER,
    get_tenant_plan_tier,
    tier_for_tool,
)
from contact_ops.mcp.errors import QUOTA_EXCEEDED, ToolError

logger = structlog.get_logger(__name__)


async def _count_tenant_contacts(db: AsyncSession) -> int:
    """COUNT the calling tenant's contacts under the RLS-bound session.

    ``person_tenant_membership`` holds one row per contact present in a
    workspace; RLS scopes the table to the GUC-bound tenant, so this COUNT can
    only ever see the caller's own rows — there is no tenant_id parameter to get
    wrong and no path to cross-tenant reads. The membership gate has already
    proven the GUC is bound by the time a metered tool reaches here.
    """
    value = await db.scalar(text("SELECT count(*) FROM person_tenant_membership"))
    return int(value or 0)


async def check_quota(
    tool_name: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    settings: Settings,
) -> ToolError | None:
    """Return a ToolError if the tenant has exhausted the plan allowance for a tool.

    Mirrors ``check_entitlement``: self-host bypasses; free tools short-circuit
    with no DB I/O; pro/enterprise tools resolve the tenant's plan + check the
    numeric allowance. Shadow-first — with ``BILLING_QUOTA_ENFORCED`` False an
    over-limit tenant is logged (``quota_would_deny``) and allowed.

    v1 enforces the locally-countable ``max_contacts`` cap only;
    ``monthly_agent_runs`` is metered to Lago and not gated here (see module
    docstring).
    """
    # Self-host operators are never metered or capped — their cell, their data.
    if settings.DEPLOYMENT_MODE == "self_host":
        return None

    # Free tools are never metered, so they are never quota-checked. Keep the
    # common path a single dict lookup with zero DB I/O (mirrors entitlement).
    required = tier_for_tool(tool_name)
    if required == DEFAULT_TIER:
        return None

    # A metered (pro/enterprise) tool: resolve the tenant's plan + its allowances.
    plan_tier = await get_tenant_plan_tier(db, tenant_id)
    plan: PlanSpec = plan_for_tier(plan_tier)

    # --- max_contacts: the one locally-countable cap (zero-migration, RLS-safe) ---
    if plan.max_contacts is not None:
        used = await _count_tenant_contacts(db)
        # ">=" because the metered tool is about to (or just did) add work on top
        # of an already-at-cap workspace; at the cap is over for the next write.
        if used >= plan.max_contacts:
            return _decide(
                settings=settings,
                tool_name=tool_name,
                tenant_id=tenant_id,
                plan_tier=plan_tier,
                limit_name="max_contacts",
                limit=plan.max_contacts,
                used=used,
            )

    # monthly_agent_runs is metered-to-Lago-only in v1 — not gated here. When a
    # local usage source exists, add its check above with the same _decide() tail.
    return None


def _decide(
    *,
    settings: Settings,
    tool_name: str,
    tenant_id: uuid.UUID,
    plan_tier: str,
    limit_name: str,
    limit: int,
    used: int,
) -> ToolError | None:
    """Shadow-first decision tail shared by every quota check.

    Shadow (``BILLING_QUOTA_ENFORCED`` False): log ``quota_would_deny`` + ALLOW.
    Enforced: log ``quota_denied`` + return a non-retryable ``QUOTA_EXCEEDED``.
    """
    if not settings.BILLING_QUOTA_ENFORCED:
        logger.info(
            "quota_would_deny",
            tool=tool_name,
            tenant_id=str(tenant_id),
            plan_tier=plan_tier,
            limit_name=limit_name,
            limit=limit,
            used=used,
        )
        return None

    logger.info(
        "quota_denied",
        tool=tool_name,
        tenant_id=str(tenant_id),
        plan_tier=plan_tier,
        limit_name=limit_name,
        limit=limit,
        used=used,
    )
    return ToolError(
        QUOTA_EXCEEDED,
        f"Your '{plan_tier}' plan allows {limit} {limit_name.replace('_', ' ')}; "
        f"you're at {used}. Upgrade your plan to raise this limit.",
        retryable=False,
        hint=plan_tier,
    )
