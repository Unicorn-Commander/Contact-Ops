"""Contact-Ops agent fleet substrate (Phase 3.0 Foundation).

This package ships the runtime, registry, trust ladder, cost guards,
observability, and base class that the Phase 3.1+ agents (Dedup, Voice Match,
Enrichment, Lifecycle, Tag, Relationship Inference, CardDAV Reconciliation,
Calibration Daemon, Data Intel Bridge, Graph Sync Worker, Communication Signal
Recomputer, Provenance Promoter) all build on.

The contract: any new agent is ~200 lines of Python that registers an
``AgentDef``, declares its inputs/outputs/reversibility, and inherits the
rest. See ``docs/AGENTS.md`` for the worked example.
"""

from contact_ops.agents.base import (
    AgentContext,
    AgentResult,
    BaseAgent,
)
from contact_ops.agents.errors import (
    AgentError,
    CircuitBreakerOpenError,
    CostBudgetExceededError,
    IdempotencyKeyReusedError,
    RetryableError,
)
from contact_ops.agents.registry import (
    AgentClass,
    AgentDef,
    Reversibility,
    get_agent,
    list_agents,
    register_agent,
)
from contact_ops.agents.trust import (
    BetaPosterior,
    TrustTier,
    tier_from_posterior,
)

__all__ = [
    "AgentClass",
    "AgentContext",
    "AgentDef",
    "AgentError",
    "AgentResult",
    "BaseAgent",
    "BetaPosterior",
    "CircuitBreakerOpenError",
    "CostBudgetExceededError",
    "IdempotencyKeyReusedError",
    "RetryableError",
    "Reversibility",
    "TrustTier",
    "get_agent",
    "list_agents",
    "register_agent",
    "tier_from_posterior",
]
