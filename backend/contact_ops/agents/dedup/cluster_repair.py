from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True)
class Edge:
    person_a_id: UUID
    person_b_id: UUID
    score: float
    weight_bits: float = 0.0


@dataclass
class Cluster:
    person_ids: set[UUID]
    edges: list[Edge]
    max_edge_score: float = 0.0
    min_edge_score: float = 0.0
    threshold_applied: float = 0.40


@dataclass
class ClusterRepairResult:
    clusters: list[Cluster] = field(default_factory=list)
    original_cluster_count: int = 0
    repaired_cluster_count: int = 0
    black_holes_detected: int = 0
    review_proposals: list[Cluster] = field(default_factory=list)


class UnionFind:
    """Disjoint-set data structure with path compression and union by rank."""

    def __init__(self) -> None:
        self._parent: dict[UUID, UUID] = {}
        self._rank: dict[UUID, int] = {}

    def find(self, x: UUID) -> UUID:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0
            return x
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, a: UUID, b: UUID) -> bool:
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return False
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
        return True

    def components(self) -> dict[UUID, set[UUID]]:
        comps: dict[UUID, set[UUID]] = {}
        for x in list(self._parent):
            root = self.find(x)
            comps.setdefault(root, set()).add(x)
        return comps


def _find_components(
    edges: list[Edge],
    threshold: float,
) -> list[Cluster]:
    """Union-find to find connected components from edges above threshold."""
    uf = UnionFind()
    used: set[UUID] = set()
    active_edges: list[Edge] = []

    for e in edges:
        if e.score >= threshold:
            uf.union(e.person_a_id, e.person_b_id)
            used.add(e.person_a_id)
            used.add(e.person_b_id)
            active_edges.append(e)

    comp_map = uf.components()
    clusters: list[Cluster] = []

    for _root, person_ids in comp_map.items():
        cluster_edges = [
            e for e in active_edges
            if e.person_a_id in person_ids and e.person_b_id in person_ids
        ]
        scores = [e.score for e in cluster_edges]
        max_score = max(scores) if scores else 0.0
        min_score = min(scores) if scores else 0.0
        clusters.append(
            Cluster(
                person_ids=person_ids,
                edges=cluster_edges,
                max_edge_score=max_score,
                min_edge_score=min_score,
                threshold_applied=threshold,
            )
        )

    singletons: set[UUID] = set()
    for e in edges:
        if e.person_a_id not in used:
            singletons.add(e.person_a_id)
        if e.person_b_id not in used:
            singletons.add(e.person_b_id)

    for pid in singletons:
        clusters.append(
            Cluster(
                person_ids={pid},
                edges=[],
                threshold_applied=threshold,
            )
        )

    return clusters


def build_components(
    edges: list[Edge],
    threshold: float = 0.40,
) -> list[Cluster]:
    """Build connected components from edges above threshold.

    Uses a simple union-find (disjoint set) data structure.
    Returns a list of Clusters, each containing person_ids and their edges.
    """
    return _find_components(edges, threshold)


def repair_clusters(
    clusters: list[Cluster],
    max_size: int = 5,
    max_threshold: float = 0.95,
    threshold_step: float = 0.05,
) -> ClusterRepairResult:
    """Repair clusters that exceed max_size.

    For any cluster with size > max_size, incrementally raise the local
    threshold by threshold_step and recompute components until all are
    <= max_size or threshold exceeds max_threshold.

    Beyond max_threshold, instead of splitting, emit a ClusterReviewProposal
    in review_proposals for manual review.
    """
    result = ClusterRepairResult(
        original_cluster_count=len(clusters),
    )

    repaired_count = 0

    for cluster in clusters:
        if len(cluster.person_ids) <= max_size:
            result.clusters.append(cluster)
            continue

        current_threshold = cluster.threshold_applied
        repaired = False

        while current_threshold <= max_threshold:
            current_threshold += threshold_step
            if current_threshold > max_threshold:
                break
            sub_clusters = _find_components(cluster.edges, current_threshold)

            all_small = all(len(c.person_ids) <= max_size for c in sub_clusters)

            if all_small:
                for sc in sub_clusters:
                    result.clusters.append(sc)
                repaired_count += 1
                repaired = True
                break

        if not repaired:
            result.black_holes_detected += 1
            result.review_proposals.append(cluster)

    result.repaired_cluster_count = repaired_count

    return result
