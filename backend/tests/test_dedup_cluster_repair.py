from __future__ import annotations

from uuid import UUID, uuid4

from contact_ops.agents.dedup.cluster_repair import (
    Cluster,
    Edge,
    build_components,
    repair_clusters,
)


def _uuid(i: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{i:012d}")


class TestBuildComponents:
    def test_simple_component(self) -> None:
        a, b, c = _uuid(1), _uuid(2), _uuid(3)
        edges = [
            Edge(person_a_id=a, person_b_id=b, score=0.80),
            Edge(person_a_id=b, person_b_id=c, score=0.75),
        ]
        clusters = build_components(edges, threshold=0.40)
        assert len(clusters) == 1
        assert clusters[0].person_ids == {a, b, c}

    def test_two_components(self) -> None:
        a, b, c, d = _uuid(1), _uuid(2), _uuid(3), _uuid(4)
        edges = [
            Edge(person_a_id=a, person_b_id=b, score=0.80),
            Edge(person_a_id=c, person_b_id=d, score=0.75),
        ]
        clusters = build_components(edges, threshold=0.40)
        assert len(clusters) == 2

    def test_threshold_filtering(self) -> None:
        a, b, c = _uuid(1), _uuid(2), _uuid(3)
        edges = [
            Edge(person_a_id=a, person_b_id=b, score=0.50),
            Edge(person_a_id=b, person_b_id=c, score=0.30),
        ]
        clusters = build_components(edges, threshold=0.40)
        # Edge b-c is below threshold, so only a-b forms a component
        comps = [c for c in clusters if len(c.person_ids) > 1]
        singletons = [c for c in clusters if len(c.person_ids) == 1]
        assert len(comps) == 1
        assert comps[0].person_ids == {a, b}
        assert len(singletons) == 1
        assert singletons[0].person_ids == {c}

    def test_empty_edges(self) -> None:
        clusters = build_components([], threshold=0.40)
        assert clusters == []


class TestRepairClusters:
    def test_component_within_max_size(self) -> None:
        a, b, c = _uuid(1), _uuid(2), _uuid(3)
        edges = [
            Edge(person_a_id=a, person_b_id=b, score=0.80),
            Edge(person_a_id=b, person_b_id=c, score=0.75),
        ]
        clusters = build_components(edges, threshold=0.40)
        result = repair_clusters(clusters, max_size=5)
        assert result.original_cluster_count == 1
        assert result.repaired_cluster_count == 0
        assert result.black_holes_detected == 0
        assert len(result.clusters) == 1

    def test_component_exceeds_max_size_incremental_raise(self) -> None:
        persons = [_uuid(i) for i in range(1, 8)]
        edges = [
            Edge(person_a_id=persons[0], person_b_id=persons[1], score=0.60),
            Edge(person_a_id=persons[1], person_b_id=persons[2], score=0.60),
            Edge(person_a_id=persons[2], person_b_id=persons[3], score=0.50),
            Edge(person_a_id=persons[3], person_b_id=persons[4], score=0.50),
            Edge(person_a_id=persons[4], person_b_id=persons[5], score=0.45),
            Edge(person_a_id=persons[5], person_b_id=persons[6], score=0.45),
        ]
        clusters = build_components(edges, threshold=0.40)
        assert len(clusters) == 1
        assert len(clusters[0].person_ids) == 7

        result = repair_clusters(clusters, max_size=5)
        assert result.repaired_cluster_count == 1
        assert result.black_holes_detected == 0
        assert all(len(c.person_ids) <= 5 for c in result.clusters)

    def test_black_hole_detected(self) -> None:
        persons = [_uuid(i) for i in range(1, 8)]
        edges = [
            Edge(person_a_id=persons[0], person_b_id=persons[1], score=0.96),
            Edge(person_a_id=persons[1], person_b_id=persons[2], score=0.96),
            Edge(person_a_id=persons[2], person_b_id=persons[3], score=0.96),
            Edge(person_a_id=persons[3], person_b_id=persons[4], score=0.96),
            Edge(person_a_id=persons[4], person_b_id=persons[5], score=0.96),
            Edge(person_a_id=persons[5], person_b_id=persons[6], score=0.96),
        ]
        clusters = build_components(edges, threshold=0.40)
        result = repair_clusters(clusters, max_size=5, max_threshold=0.95)
        assert result.black_holes_detected == 1
        assert len(result.review_proposals) == 1
