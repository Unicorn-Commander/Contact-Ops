"""Prometheus metric registry for the agent fleet.

Names follow the ``contactops_agent_*`` convention (Phase 3 Design §8) so
the centerdeep Prometheus stack can scope these alongside other ecosystem
dashboards.

The ``/metrics`` endpoint on the FastAPI app exposes these for Prometheus
scrape; ``metrics_endpoint`` is the handler that ``main.py`` mounts.
"""

from __future__ import annotations

from fastapi import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Dedicated registry keeps the agent metrics surface separate from any
# other Prometheus client in the same process (notably FastAPI's default
# auto-instrumentation).
REGISTRY = CollectorRegistry()


# ----- core fleet metrics -----

AGENT_LATENCY_SECONDS = Histogram(
    "contactops_agent_latency_seconds",
    "Agent run latency",
    labelnames=("agent", "tenant", "tier"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=REGISTRY,
)

AGENT_SUCCESS_TOTAL = Counter(
    "contactops_agent_success_total",
    "Outcomes per agent run (succeeded, errored)",
    labelnames=("agent", "tenant", "outcome"),
    registry=REGISTRY,
)

AGENT_REVERT_RATE_24H = Gauge(
    "contactops_agent_revert_rate_24h",
    "Rolling 24h revert rate per agent",
    labelnames=("agent", "tenant"),
    registry=REGISTRY,
)

AGENT_TOKENS_TOTAL = Counter(
    "contactops_agent_tokens_total",
    "Tokens used by agent LLM calls",
    labelnames=("agent", "model", "tenant", "kind"),
    registry=REGISTRY,
)

AGENT_COST_CENTS_TOTAL = Counter(
    "contactops_agent_cost_cents_total",
    "Cents spent on agent LLM calls",
    labelnames=("agent", "model", "tenant"),
    registry=REGISTRY,
)

AGENT_QUEUE_DEPTH = Gauge(
    "contactops_agent_queue_depth",
    "Pending tasks per agent + tier",
    labelnames=("agent", "tier"),
    registry=REGISTRY,
)

AGENT_LAG_SECONDS = Gauge(
    "contactops_agent_lag_seconds",
    "Oldest unprocessed event age per agent",
    labelnames=("agent",),
    registry=REGISTRY,
)

AGENT_TRUST_BETA = Gauge(
    "contactops_agent_trust_beta",
    "Beta posterior parameter per agent + tenant",
    labelnames=("agent", "tenant", "param"),
    registry=REGISTRY,
)

AGENT_CIRCUIT_BREAKER_OPEN = Gauge(
    "contactops_agent_circuit_breaker_open",
    "1 = circuit breaker is open, 0 = closed",
    labelnames=("scope",),  # "fleet" or "agent:<slug>"
    registry=REGISTRY,
)

AGENT_DLQ_DEPTH = Gauge(
    "contactops_agent_dlq_depth",
    "Number of unresolved DLQ entries",
    labelnames=("agent", "error_class"),
    registry=REGISTRY,
)

AUDIT_WRITE_FAILURES_TOTAL = Counter(
    "contactops_audit_write_failures_total",
    "HTTP audit middleware writes that failed",
    labelnames=("tenant",),
    registry=REGISTRY,
)


async def metrics_endpoint() -> Response:
    """FastAPI handler exposing the metrics in Prometheus text exposition format."""
    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


def set_trust_beta(
    *,
    agent: str,
    tenant: str,
    alpha: float,
    beta: float,
    mean: float,
    lower_ci: float,
) -> None:
    """Snapshot the Beta(α, β) posterior to the trust gauge."""
    AGENT_TRUST_BETA.labels(agent=agent, tenant=tenant, param="alpha").set(alpha)
    AGENT_TRUST_BETA.labels(agent=agent, tenant=tenant, param="beta").set(beta)
    AGENT_TRUST_BETA.labels(agent=agent, tenant=tenant, param="mean").set(mean)
    AGENT_TRUST_BETA.labels(agent=agent, tenant=tenant, param="lower_ci").set(lower_ci)


__all__ = [
    "AGENT_CIRCUIT_BREAKER_OPEN",
    "AGENT_COST_CENTS_TOTAL",
    "AGENT_DLQ_DEPTH",
    "AGENT_LAG_SECONDS",
    "AGENT_LATENCY_SECONDS",
    "AGENT_QUEUE_DEPTH",
    "AGENT_REVERT_RATE_24H",
    "AGENT_SUCCESS_TOTAL",
    "AGENT_TOKENS_TOTAL",
    "AGENT_TRUST_BETA",
    "AUDIT_WRITE_FAILURES_TOTAL",
    "REGISTRY",
    "metrics_endpoint",
    "set_trust_beta",
]
