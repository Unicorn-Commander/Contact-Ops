"""Billing substrate — suite-Lago metering + plan quotas (dormant by default).

Reuses the entitlement tier model (TIER_LADDER / tenants.plan_tier) verbatim;
adds the numeric plan catalog, the quota gate, and the BILLING_PROVIDER metering
abstraction. Everything is off until BILLING_PROVIDER/LAGO_* + BILLING_QUOTA_
ENFORCED are configured.
"""

from contact_ops.billing.catalog import (  # noqa: F401
    AGENT_RUN_METRIC,
    PLAN_CATALOG,
    PlanSpec,
    plan_for_tier,
)
from contact_ops.billing.provider import (  # noqa: F401
    BillingProvider,
    get_billing_provider,
)
from contact_ops.billing.quota import check_quota  # noqa: F401

__all__ = [
    "AGENT_RUN_METRIC",
    "PLAN_CATALOG",
    "BillingProvider",
    "PlanSpec",
    "check_quota",
    "get_billing_provider",
    "plan_for_tier",
]
