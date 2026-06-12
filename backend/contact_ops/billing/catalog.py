"""Plan catalog — the single source of truth for numeric quotas + Lago plan
mapping (P-00075 billing slab).

Division of responsibility with ``contact_ops/mcp/entitlement.py``:

  * ``entitlement.py`` owns **per-tool gating** — which tools a tier may call
    (TIER_LADDER / _PRO_TOOLS / tier_for_tool / check_entitlement). It answers
    "is this tool allowed on this plan?".
  * ``catalog.py`` (this file) owns the **numbers** — per-tier resource caps
    (max_contacts, monthly_agent_runs) and the Lago plan / billable-metric
    mapping. It answers "how much, and what does it bill?".

The quota gate and the metering provider both read PLAN_CATALOG so they can never
drift from the tier strings entitlement already uses. The two modules share the
SAME tier keys ('free' / 'pro' / 'enterprise' — exactly TIER_LADDER's keys).

This is a PURE DATA module: no I/O, no Settings, no imports beyond stdlib. The
free/paid line itself ("gate on compute" — the metered ops are exactly the GPU
tools in entitlement._PRO_TOOLS) lives in entitlement.py; here we just attach the
billable-metric code to the tiers that emit it.

The CO billable metric for one metered GPU op is ``contact_ops_agent_run``. A
metered tool dispatch (an entitlement pro tool that actually ran our hardware)
emits exactly one of these events to suite Lago.

NOTE — numbers are PLACEHOLDERS Aaron tunes later (search "TUNE"). They are not
load-bearing for correctness; the SHAPE (tiers, metric codes, None=unlimited) is.
"""

from __future__ import annotations

from dataclasses import dataclass

# CO billable metric code for one metered GPU op. Keep in sync with the Lago
# metric the metering provider emits and with any Lago-seeded billable_metric.
AGENT_RUN_METRIC = "contact_ops_agent_run"

# Default tier — mirrors entitlement.DEFAULT_TIER. Kept as a literal (not an
# import) to preserve this module's purity / zero coupling; both are 'free'.
DEFAULT_TIER = "free"


@dataclass(frozen=True)
class PlanSpec:
    """Per-tier billing + quota spec. Shared contract across the billing slab.

    Attributes:
        tier: 'free' | 'pro' | 'enterprise' — the SAME strings as TIER_LADDER.
        lago_plan_code: suite Lago plan code this tier maps to.
        max_contacts: contact-count cap; ``None`` means unlimited.
        monthly_agent_runs: metered GPU runs/month cap; ``None`` means unlimited.
        billable_metrics: Lago metric codes this tier emits (empty for free).
    """

    tier: str
    lago_plan_code: str
    max_contacts: int | None
    monthly_agent_runs: int | None
    billable_metrics: tuple[str, ...]


# --- Catalog ----------------------------------------------------------------
# Lago plan-code mapping (suite Lago seeded plans: trial / starter $19 /
# professional $49 / enterprise $99):
#   free       -> 'trial'         the seeded free/onboarding plan. (There is no
#                                 'contact_ops_free' plan on suite Lago today, so
#                                 we point at the shared 'trial' plan rather than
#                                 invent a code that 422s on event ingest. If a
#                                 CO-specific free plan is ever seeded, swap this
#                                 one string.)
#   pro        -> 'professional'  the $49 suite plan.
#   enterprise -> 'enterprise'    the $99 suite plan.
# (The $19 'starter' plan is intentionally unused by CO's 3-rung ladder.)
#
# billable_metrics: pro + enterprise emit 'contact_ops_agent_run' for metered
# GPU ops; free emits nothing (it never touches a GPU — see entitlement.py).
PLAN_CATALOG: dict[str, PlanSpec] = {
    "free": PlanSpec(
        tier="free",
        lago_plan_code="trial",
        max_contacts=1_000,        # TUNE (placeholder)
        monthly_agent_runs=50,     # TUNE (placeholder)
        billable_metrics=(),
    ),
    "pro": PlanSpec(
        tier="pro",
        lago_plan_code="professional",
        max_contacts=None,                 # unlimited
        monthly_agent_runs=5_000,          # TUNE (placeholder) — generous allowance
        billable_metrics=(AGENT_RUN_METRIC,),
    ),
    "enterprise": PlanSpec(
        tier="enterprise",
        lago_plan_code="enterprise",
        max_contacts=None,                 # unlimited
        monthly_agent_runs=None,           # unlimited
        billable_metrics=(AGENT_RUN_METRIC,),
    ),
}


def plan_for_tier(tier: str) -> PlanSpec:
    """Resolve a PlanSpec for a tier string. Unknown tier falls back to free.

    Fails SAFE the same way entitlement.get_tenant_plan_tier does: an
    unrecognized / missing tier resolves to the free spec (smallest caps, no
    billable metrics), never to a more-permissive plan.
    """
    return PLAN_CATALOG.get(tier, PLAN_CATALOG[DEFAULT_TIER])
