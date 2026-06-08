"""Graph MCP tools backed by tenant-scoped FalkorDB graphs."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from contact_ops.agents.graph_sync.cypher_queries import (
    DUPLICATES_GRAPH,
    EGO_GRAPH,
    MUTUAL_CONNECTIONS,
    PATH_THROUGH_TOPIC,
    SHORTEST_PATH,
    SUGGEST_INTRO,
    WHO_KNOWS,
)
from contact_ops.agents.graph_sync.falkordb_client import (
    FalkorDBGraphClient,
    GraphQueryResult,
    TenantGraph,
    graph_name_for_slug,
)
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools.common import ToolOutput, register
from contact_ops.models import Tenant


class ShortestPathInput(BaseModel):
    from_person_id: uuid.UUID
    to_person_id: uuid.UUID
    max_hops: int = Field(default=5, ge=1, le=8)


class WhoKnowsInput(BaseModel):
    at_organization_id: uuid.UUID
    role_filter: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=50, ge=1, le=100)


class MutualConnectionsInput(BaseModel):
    person_a_id: uuid.UUID
    person_b_id: uuid.UUID
    limit: int = Field(default=50, ge=1, le=100)


class SuggestIntroInput(BaseModel):
    from_person_id: uuid.UUID
    to_topic_or_org: str = Field(max_length=120)
    limit: int = Field(default=5, ge=1, le=20)


class FindClustersInput(BaseModel):
    person_id: uuid.UUID
    hop_limit: int = Field(default=2, ge=1, le=4)


class ExtractEgoGraphInput(BaseModel):
    person_id: uuid.UUID
    hop_limit: int = Field(default=2, ge=1, le=4)
    edge_kinds: list[str] | None = None
    limit: int = Field(default=500, ge=50, le=2500)


class FindPathThroughTopicInput(BaseModel):
    person_id: uuid.UUID
    topic_id: uuid.UUID
    max_hops: int = Field(default=4, ge=2, le=6)
    limit: int = Field(default=20, ge=1, le=100)


class FindDuplicatesGraphInput(BaseModel):
    person_id: uuid.UUID
    limit: int = Field(default=25, ge=1, le=100)


class GraphOverviewInput(BaseModel):
    limit: int = Field(default=1000, ge=50, le=2500)


class GraphNode(BaseModel):
    id: str
    name: str
    kind: str
    group: str
    val: int = 4
    tenant_id: str | None = None


class GraphLink(BaseModel):
    source: str
    target: str
    type: str
    strength: float | None = None


class EgoGraphOutput(ToolOutput):
    nodes: list[GraphNode]
    links: list[GraphLink]
    node_count: int
    edge_count: int
    truncated: bool = False


class ItemsOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int


async def _tenant_graph(ctx: MCPContext) -> TenantGraph:
    tenant = await ctx.db.scalar(select(Tenant).where(Tenant.id == ctx.tenant_id))
    if tenant is None:
        raise ValueError("tenant not found")
    graph_name = tenant.graph_name or graph_name_for_slug(tenant.slug)
    return TenantGraph(tenant_id=tenant.id, graph_name=graph_name, graph_mode=tenant.graph_mode)


async def _query(ctx: MCPContext, cypher: str, params: dict[str, Any]) -> GraphQueryResult:
    client = FalkorDBGraphClient()
    try:
        return await client.query(await _tenant_graph(ctx), cypher, params)
    finally:
        await client.close()


def _simple_items(result: GraphQueryResult) -> ItemsOutput:
    return ItemsOutput(items=[{"row": row} for row in result.rows], count=len(result.rows))


async def shortest_path(ctx: MCPContext, input: ShortestPathInput) -> ItemsOutput:
    result = await _query(
        ctx,
        SHORTEST_PATH,
        {
            "from_person_id": str(input.from_person_id),
            "to_person_id": str(input.to_person_id),
            "max_hops": input.max_hops,
        },
    )
    return _simple_items(result)


async def who_knows(ctx: MCPContext, input: WhoKnowsInput) -> ItemsOutput:
    result = await _query(
        ctx,
        WHO_KNOWS,
        {
            "organization_id": str(input.at_organization_id),
            "role_filter": input.role_filter,
            "limit": input.limit,
        },
    )
    return _simple_items(result)


async def mutual_connections(ctx: MCPContext, input: MutualConnectionsInput) -> ItemsOutput:
    result = await _query(
        ctx,
        MUTUAL_CONNECTIONS,
        {
            "person_a_id": str(input.person_a_id),
            "person_b_id": str(input.person_b_id),
            "limit": input.limit,
        },
    )
    return _simple_items(result)


async def suggest_intro(ctx: MCPContext, input: SuggestIntroInput) -> ItemsOutput:
    result = await _query(
        ctx,
        SUGGEST_INTRO,
        {
            "from_person_id": str(input.from_person_id),
            "target_id": input.to_topic_or_org,
            "target_label": input.to_topic_or_org,
            "limit": input.limit,
        },
    )
    return _simple_items(result)


async def find_clusters(ctx: MCPContext, input: FindClustersInput) -> ItemsOutput:
    result = await extract_ego_graph(
        ctx,
        ExtractEgoGraphInput(person_id=input.person_id, hop_limit=input.hop_limit, limit=1000),
    )
    groups: dict[str, int] = {}
    for node in result.nodes:
        groups[node.group] = groups.get(node.group, 0) + 1
    return ItemsOutput(
        items=[{"cluster_id": key, "size": value} for key, value in sorted(groups.items())],
        count=len(groups),
    )


async def extract_ego_graph(ctx: MCPContext, input: ExtractEgoGraphInput) -> EgoGraphOutput:
    result = await _query(
        ctx,
        EGO_GRAPH,
        {"person_id": str(input.person_id), "hops": input.hop_limit, "limit": input.limit},
    )
    nodes: dict[str, GraphNode] = {}
    links: list[GraphLink] = []
    for row in result.rows:
        if len(row) < 8:
            continue
        (
            source_id,
            source_name,
            source_labels,
            target_id,
            target_name,
            target_labels,
            edge_type,
            confidence,
        ) = row[:8]
        source_key = str(source_id)
        nodes[source_key] = GraphNode(
            id=source_key,
            name=str(source_name or source_id),
            kind=_first_label(source_labels),
            group=_first_label(source_labels),
            val=8,
            tenant_id=str(ctx.tenant_id),
        )
        # OPTIONAL MATCH (ego) returns the center even with no neighbor — that
        # row has a null target. Keep the center node, skip the null edge.
        if not _is_present(target_id):
            continue
        target_key = str(target_id)
        nodes[target_key] = GraphNode(
            id=target_key,
            name=str(target_name or target_id),
            kind=_first_label(target_labels),
            group=_first_label(target_labels),
            tenant_id=str(ctx.tenant_id),
        )
        links.append(
            GraphLink(
                source=source_key,
                target=target_key,
                type=str(edge_type),
                strength=_to_float(confidence),
            )
        )
    return EgoGraphOutput(
        nodes=list(nodes.values()),
        links=links,
        node_count=len(nodes),
        edge_count=len(links),
        truncated=len(nodes) >= input.limit,
    )


# Tenant-wide overview: every edge (and the nodes it touches), so the default
# graph view shows the whole connected network (org clusters) instead of
# requiring the user to first pick one person. Same 8-column row shape as
# EGO_GRAPH so the output construction below is identical.
GRAPH_OVERVIEW = """
MATCH (a)-[r]->(b)
RETURN a.id,
       coalesce(a.display_name, a.name, a.address, a.e164),
       labels(a),
       b.id,
       coalesce(b.display_name, b.name, b.address, b.e164),
       labels(b),
       type(r),
       r.confidence
LIMIT $limit
"""


async def graph_overview(ctx: MCPContext, input: GraphOverviewInput) -> EgoGraphOutput:
    result = await _query(ctx, GRAPH_OVERVIEW, {"limit": input.limit})
    nodes: dict[str, GraphNode] = {}
    links: list[GraphLink] = []
    for row in result.rows:
        if len(row) < 8:
            continue
        (
            source_id,
            source_name,
            source_labels,
            target_id,
            target_name,
            target_labels,
            edge_type,
            confidence,
        ) = row[:8]
        source_key = str(source_id)
        nodes[source_key] = GraphNode(
            id=source_key,
            name=str(source_name or source_id),
            kind=_first_label(source_labels),
            group=_first_label(source_labels),
            val=8,
            tenant_id=str(ctx.tenant_id),
        )
        # OPTIONAL MATCH (ego) returns the center even with no neighbor — that
        # row has a null target. Keep the center node, skip the null edge.
        if not _is_present(target_id):
            continue
        target_key = str(target_id)
        nodes[target_key] = GraphNode(
            id=target_key,
            name=str(target_name or target_id),
            kind=_first_label(target_labels),
            group=_first_label(target_labels),
            tenant_id=str(ctx.tenant_id),
        )
        links.append(
            GraphLink(
                source=source_key,
                target=target_key,
                type=str(edge_type),
                strength=_to_float(confidence),
            )
        )
    return EgoGraphOutput(
        nodes=list(nodes.values()),
        links=links,
        node_count=len(nodes),
        edge_count=len(links),
        truncated=len(nodes) >= input.limit,
    )


async def find_path_through_topic(ctx: MCPContext, input: FindPathThroughTopicInput) -> ItemsOutput:
    result = await _query(
        ctx,
        PATH_THROUGH_TOPIC,
        {
            "person_id": str(input.person_id),
            "topic_id": str(input.topic_id),
            "max_hops": input.max_hops,
            "limit": input.limit,
        },
    )
    return _simple_items(result)


async def find_duplicates_graph(ctx: MCPContext, input: FindDuplicatesGraphInput) -> ItemsOutput:
    result = await _query(
        ctx,
        DUPLICATES_GRAPH,
        {"person_id": str(input.person_id), "limit": input.limit},
    )
    return _simple_items(result)


def _first_label(labels: Any) -> str:
    if isinstance(labels, list) and labels:
        return str(labels[0])
    # Non-compact GRAPH.QUERY returns a node's labels as a string like
    # "[Person]" (or "[Person, Foo]"), not a Python list — unwrap it.
    if isinstance(labels, str):
        inner = labels.strip().strip("[]").strip()
        if inner:
            return inner.split(",")[0].strip()
    return "Entity"


def _to_float(value: Any) -> float | None:
    """Parse a confidence/strength value that FalkorDB returns as a string."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_present(value: Any) -> bool:
    """True unless the value is a FalkorDB null (None or the empty/"null" string)."""
    if value is None:
        return False
    return str(value).strip().lower() not in ("", "null", "none")


def register_graph_tools() -> None:
    if get_tool("extract_ego_graph") is not None:
        return
    # Graph reads are the same sensitivity as reading contacts, so gate them on
    # the same role+scope the People views use (CLIENT + person:read). The old
    # contactops:graph.read scope was never seeded in Keycloak, so every graph
    # query 403'd — masked only because the viewer was always empty.
    required_role = "CLIENT"
    required_scopes = ("person:read",)
    annotations = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    idempotency = "read-only"
    register(
        name="shortest_path",
        description="Return the shortest graph path between two people in the caller tenant graph.",
        input_model=ShortestPathInput,
        output_model=ItemsOutput,
        handler=shortest_path,
        required_role=required_role,
        required_scopes=required_scopes,
        annotations=annotations,
        idempotency=idempotency,
    )
    register(
        name="who_knows",
        description="Return people in the caller tenant who work at a target organization.",
        input_model=WhoKnowsInput,
        output_model=ItemsOutput,
        handler=who_knows,
        required_role=required_role,
        required_scopes=required_scopes,
        annotations=annotations,
        idempotency=idempotency,
    )
    register(
        name="mutual_connections",
        description="Return people connected to both input people in the caller tenant graph.",
        input_model=MutualConnectionsInput,
        output_model=ItemsOutput,
        handler=mutual_connections,
        required_role=required_role,
        required_scopes=required_scopes,
        annotations=annotations,
        idempotency=idempotency,
    )
    register(
        name="suggest_intro",
        description="Rank likely introduction brokers in the caller tenant graph.",
        input_model=SuggestIntroInput,
        output_model=ItemsOutput,
        handler=suggest_intro,
        required_role=required_role,
        required_scopes=required_scopes,
        annotations=annotations,
        idempotency=idempotency,
    )
    register(
        name="find_clusters",
        description="Summarize ego-graph clusters for one person.",
        input_model=FindClustersInput,
        output_model=ItemsOutput,
        handler=find_clusters,
        required_role=required_role,
        required_scopes=required_scopes,
        annotations=annotations,
        idempotency=idempotency,
    )
    register(
        name="extract_ego_graph",
        description="Return react-force-graph-3d shaped ego graph data.",
        input_model=ExtractEgoGraphInput,
        output_model=EgoGraphOutput,
        handler=extract_ego_graph,
        required_role=required_role,
        required_scopes=required_scopes,
        annotations=annotations,
        idempotency=idempotency,
    )
    register(
        name="graph_overview",
        description="Tenant-wide graph overview (all connected nodes/edges) for the default graph view.",
        input_model=GraphOverviewInput,
        output_model=EgoGraphOutput,
        handler=graph_overview,
        required_role=required_role,
        required_scopes=required_scopes,
        annotations=annotations,
        idempotency=idempotency,
    )
    register(
        name="find_path_through_topic",
        description="Return graph paths from a person to a topic node.",
        input_model=FindPathThroughTopicInput,
        output_model=ItemsOutput,
        handler=find_path_through_topic,
        required_role=required_role,
        required_scopes=required_scopes,
        annotations=annotations,
        idempotency=idempotency,
    )
    register(
        name="find_duplicates_graph",
        description="Return duplicate candidates connected by graph duplicate edges.",
        input_model=FindDuplicatesGraphInput,
        output_model=ItemsOutput,
        handler=find_duplicates_graph,
        required_role=required_role,
        required_scopes=required_scopes,
        annotations=annotations,
        idempotency=idempotency,
    )
