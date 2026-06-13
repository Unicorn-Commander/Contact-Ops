"""Plan-tier entitlement policy — single source of truth (P-00075 §4).

The free/paid line is **"gate on compute cost"**, decided with Aaron 2026-06-11:

  free       reads + MCP/UI CRUD + deterministic (exact/Soundex) dedup + graph
             queries + consent management. Cheap for us — their compute (their
             AI's tokens via MCP, or their clicks) drives DB-only work. This is
             the funnel; making it generous costs us almost nothing because it
             never touches a GPU.
  pro        our-GPU autonomous agents: ML dedup (embeddings + LLM tie-breaker),
             enrichment, voice fingerprint/match, bulk import, connector sync.
             The expensive work runs on OUR hardware.
  enterprise compliance + control: HIPAA/GDPR/residency, SSO/SCIM, audit export,
             suite federation, self-host license/support. (No tools map here yet
             — those features are net-new; the rung exists so the ladder is
             ready when they land.)

Why a central map instead of a ``tier_required`` field scattered across ~10 tool
modules: the policy is reviewed and adjusted as one unit, and BOTH the dispatch
gate (server.py) and the Brigade manifest (brigade_registration.py) resolve tier
from here, so they can never drift. Re-tagging the free/paid line = editing one
set in one file.

Enforcement is SHADOW-FIRST (settings.ENTITLEMENT_ENFORCED, default False): the
gate logs what it WOULD deny and allows the call until the flag is flipped. A
``self_host`` deployment bypasses gating entirely (their cell, their data).
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.core.config import Settings
from contact_ops.mcp.errors import ENTITLEMENT_REQUIRED, ToolError

logger = structlog.get_logger(__name__)

# Ordered ladder — a plan satisfies a tool when its rung >= the tool's rung.
TIER_LADDER: dict[str, int] = {"free": 0, "pro": 1, "enterprise": 2}
DEFAULT_TIER = "free"

# --- Pro tools: run our autonomous agents / GPU / bulk pipelines -------------
# Note: cheap siblings stay free on purpose — e.g. dedup_status (read),
# list_voice_samples (read), record_consent/revoke_voice_consent (compliance:
# never paywall consent), and data_quality.merge_people (manual deterministic
# merge) are all free; only the agent/GPU triggers below are pro.
_PRO_TOOLS: frozenset[str] = frozenset(
    {
        # Enrichment (autonomous inference that writes orgs + edges)
        "infer_orgs_from_email_domains",
        # ML dedup — propose_merge runs the embedding + LLM tie-breaker pipeline
        "propose_merge",
        # Voice — fingerprint extraction / matching (GPU)
        "match_speaker",
        "upload_voice_sample",
        "confirm_voice_upload",
        "enroll_voice",
        # NB: CSV/vCard import (propose_csv_records / propose_vcard_records) is
        # deliberately FREE — it's the first-run on-ramp every contact manager
        # gives away, and by "gate on compute" it's cheap (parse + propose rows,
        # DB-only). The EXPENSIVE post-import work — ML dedup (propose_merge) +
        # enrichment — stays pro, which is the right place to gate.
        # Autonomous-agent fleet control + calibration (the pro agents' admin)
        "promote_agent_tier",
        "demote_agent_tier",
        "pause_agent",
        "resume_agent",
        "drain_dlq",
        "run_calibration_now",
        "apply_tier_promotion",
    }
)

# --- Enterprise tools: compliance / SSO / federation -------------------------
# The GDPR export + Art.17 erasure admin tools (P-00075 compliance slab). They
# gate to the enterprise rung; shadow-first while ENTITLEMENT_ENFORCED is False.
_ENTERPRISE_TOOLS: frozenset[str] = frozenset(
    {
        "export_subject_data",
        "export_tenant_data",
        "erase_subject",
        "erase_tenant_account",
    }
)


def tier_for_tool(tool_name: str) -> str:
    """Resolve the minimum plan tier a tool requires. Default: free."""
    if tool_name in _ENTERPRISE_TOOLS:
        return "enterprise"
    if tool_name in _PRO_TOOLS:
        return "pro"
    return DEFAULT_TIER


def _satisfies(plan_tier: str, required_tier: str) -> bool:
    return TIER_LADDER.get(plan_tier, 0) >= TIER_LADDER.get(required_tier, 0)


async def get_tenant_plan_tier(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    """Read the calling tenant's plan tier within the RLS-bound session.

    ``tenants_select`` (migration 0015) lets a bound session read its own row,
    so this resolves under the same GUC the membership gate already set. Fails
    SAFE: a missing/unreadable row resolves to ``free`` — which, combined with
    the gate only restricting pro/enterprise tools, denies (in enforce mode)
    rather than over-grants. The membership gate has already proven the GUC is
    bound by the time we get here, so the read will succeed for real tenants.
    """
    value = await db.scalar(
        text("SELECT plan_tier FROM tenants WHERE id = CAST(:t AS uuid)"),
        {"t": str(tenant_id)},
    )
    return str(value) if value else DEFAULT_TIER


async def check_entitlement(
    *,
    tool_name: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    settings: Settings,
) -> ToolError | None:
    """Return a ToolError if the tenant's plan does not cover the tool.

    Shadow mode (settings.ENTITLEMENT_ENFORCED False) returns None (allow) after
    logging ``entitlement_would_deny``. Self-host bypasses entirely. Free tools
    never query the DB — the common path stays a dict lookup with zero I/O.
    """
    # Self-host operators get everything — their cell, their data.
    if settings.DEPLOYMENT_MODE == "self_host":
        return None

    required = tier_for_tool(tool_name)
    if required == DEFAULT_TIER:
        return None  # free tool — no plan lookup, no cost

    plan = await get_tenant_plan_tier(db, tenant_id)
    if _satisfies(plan, required):
        return None

    if not settings.ENTITLEMENT_ENFORCED:
        # Shadow: record the would-be denial for review, then allow.
        logger.info(
            "entitlement_would_deny",
            tool=tool_name,
            required_tier=required,
            plan_tier=plan,
            tenant_id=str(tenant_id),
        )
        return None

    logger.info(
        "entitlement_denied",
        tool=tool_name,
        required_tier=required,
        plan_tier=plan,
        tenant_id=str(tenant_id),
    )
    return ToolError(
        ENTITLEMENT_REQUIRED,
        f"This tool requires the '{required}' plan; your workspace is on "
        f"'{plan}'. Upgrade to unlock it.",
        retryable=False,
        hint=required,
    )
