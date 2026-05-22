from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from contact_ops.agents.observability.metrics import REGISTRY

# Dedup-specific metrics

DEDUP_PRECISION = Gauge(
    "contactops_agent_dedup_precision",
    "Dedup agent precision (rolling window)",
    labelnames=("tenant",),
    registry=REGISTRY,
)

DEDUP_RECALL = Gauge(
    "contactops_agent_dedup_recall",
    "Dedup agent recall (rolling window)",
    labelnames=("tenant",),
    registry=REGISTRY,
)

DEDUP_PER_SOURCE_FP_RATE = Gauge(
    "contactops_agent_dedup_per_source_fp_rate",
    "Per-source false positive rate for dedup",
    labelnames=("tenant", "source_kind"),
    registry=REGISTRY,
)

DEDUP_CLUSTER_SIZE_HISTOGRAM = Histogram(
    "contactops_agent_dedup_cluster_size",
    "Distribution of cluster sizes after repair",
    labelnames=("tenant",),
    buckets=(1, 2, 3, 4, 5, 10, 20, 50),
    registry=REGISTRY,
)

DEDUP_THRESHOLD_DRIFT = Gauge(
    "contactops_agent_dedup_threshold_drift",
    "PSI drift score for per-source thresholds",
    labelnames=("tenant", "source_kind"),
    registry=REGISTRY,
)

DEDUP_CANDIDATES_SCORED = Counter(
    "contactops_agent_dedup_candidates_scored_total",
    "Total candidate pairs scored",
    labelnames=("tenant",),
    registry=REGISTRY,
)

DEDUP_PROPOSALS_EMITTED = Counter(
    "contactops_agent_dedup_proposals_emitted_total",
    "Merge proposals emitted",
    labelnames=("tenant", "band"),
    registry=REGISTRY,
)

DEDUP_MERGES_APPLIED = Counter(
    "contactops_agent_dedup_merges_applied_total",
    "Merges applied (auto + manual approval)",
    labelnames=("tenant", "method"),
    registry=REGISTRY,
)

DEDUP_MERGES_REVERTED = Counter(
    "contactops_agent_dedup_merges_reverted_total",
    "Merges reverted via unmerge",
    labelnames=("tenant",),
    registry=REGISTRY,
)


def set_precision(tenant_id: str, precision: float) -> None:
    DEDUP_PRECISION.labels(tenant=tenant_id).set(precision)


def set_recall(tenant_id: str, recall: float) -> None:
    DEDUP_RECALL.labels(tenant=tenant_id).set(recall)


def set_source_fp_rate(tenant_id: str, source_kind: str, rate: float) -> None:
    DEDUP_PER_SOURCE_FP_RATE.labels(tenant=tenant_id, source_kind=source_kind).set(rate)


def set_threshold_drift(tenant_id: str, source_kind: str, psi: float) -> None:
    DEDUP_THRESHOLD_DRIFT.labels(tenant=tenant_id, source_kind=source_kind).set(psi)


__all__ = [
    "DEDUP_PRECISION",
    "DEDUP_RECALL",
    "DEDUP_PER_SOURCE_FP_RATE",
    "DEDUP_CLUSTER_SIZE_HISTOGRAM",
    "DEDUP_THRESHOLD_DRIFT",
    "DEDUP_CANDIDATES_SCORED",
    "DEDUP_PROPOSALS_EMITTED",
    "DEDUP_MERGES_APPLIED",
    "DEDUP_MERGES_REVERTED",
    "set_precision",
    "set_recall",
    "set_source_fp_rate",
    "set_threshold_drift",
]
