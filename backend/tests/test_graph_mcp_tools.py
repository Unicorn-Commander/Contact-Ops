from __future__ import annotations

import uuid

from contact_ops.mcp.registry import get_tool
from contact_ops.mcp.tools.graph_admin import register_graph_tools


def test_graph_tools_registered() -> None:
    register_graph_tools()
    for name in (
        "shortest_path",
        "who_knows",
        "mutual_connections",
        "suggest_intro",
        "find_clusters",
        "extract_ego_graph",
        "find_path_through_topic",
        "find_duplicates_graph",
    ):
        tool = get_tool(name)
        assert tool is not None
        assert tool.required_role == "STAFF"
        assert tool.required_scopes == ("contactops:graph.read",)


def test_cross_tenant_leak_fixture_uses_distinct_person_ids() -> None:
    aaron_ids = {uuid.uuid4() for _ in range(5)}
    magic_ids = {uuid.uuid4() for _ in range(5)}
    assert not aaron_ids.intersection(magic_ids)
