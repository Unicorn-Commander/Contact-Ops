"""FalkorDB health checks for graph sync."""

from __future__ import annotations

from contact_ops.agents.graph_sync.falkordb_client import (
    FalkorDBGraphClient,
    TenantGraph,
    validate_graph_name,
)


async def check_falkordb(
    client: FalkorDBGraphClient,
    tenant_graph: TenantGraph,
) -> dict[str, object]:
    graph_name = validate_graph_name(tenant_graph.graph_name)
    ok = await client.ping()
    return {"ok": ok, "graph_name": graph_name, "graph_mode": tenant_graph.graph_mode}
