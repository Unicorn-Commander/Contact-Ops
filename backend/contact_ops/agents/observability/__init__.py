"""Observability for the agent fleet (OTel + Prometheus).

See ``docs/AGENTS_DEPLOY.md`` for the Laminar collector deployment and
the centerdeep Prometheus scrape configuration.
"""

from contact_ops.agents.observability.metrics import (
    AGENT_COST_CENTS_TOTAL,
    AGENT_LAG_SECONDS,
    AGENT_LATENCY_SECONDS,
    AGENT_QUEUE_DEPTH,
    AGENT_REVERT_RATE_24H,
    AGENT_SUCCESS_TOTAL,
    AGENT_TOKENS_TOTAL,
    AGENT_TRUST_BETA,
    metrics_endpoint,
)
from contact_ops.agents.observability.otel import (
    GenAIAttributes,
    agent_span,
    init_otel,
    tracer,
)

__all__ = [
    "AGENT_COST_CENTS_TOTAL",
    "AGENT_LAG_SECONDS",
    "AGENT_LATENCY_SECONDS",
    "AGENT_QUEUE_DEPTH",
    "AGENT_REVERT_RATE_24H",
    "AGENT_SUCCESS_TOTAL",
    "AGENT_TOKENS_TOTAL",
    "AGENT_TRUST_BETA",
    "GenAIAttributes",
    "agent_span",
    "init_otel",
    "metrics_endpoint",
    "tracer",
]
