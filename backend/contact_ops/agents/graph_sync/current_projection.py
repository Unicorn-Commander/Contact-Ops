"""Current relational contacts and active employments, never historical graph claims.

A reference-only outbox row always reloads the latest source. Only nodes/edges
marked with this projection and the exact tenant are removed. Other graph
channels and manually maintained relationships are outside this projection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from contact_ops.agents.graph_sync.falkordb_client import TenantGraph, graph_name_for_slug

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from contact_ops.agents.graph_sync.falkordb_client import FalkorDBGraphClient
    from contact_ops.agents.graph_sync.worker import GraphOutboxRow

PROJECTION = "current_affiliations_v1"
# One transaction-scoped lock serializes only this reference projection drain.
# Legacy edge channels retain their existing behavior and are not locked here.
DRAIN_LOCK = 419493049


class ProjectionOwnershipError(RuntimeError):
    """Existing graph data is not exclusively owned by this projection."""


def _node_write(label, name_field):
    # The ownership condition and creation are one graph write query. A
    # concurrent legacy writer cannot be adopted between preflight and MERGE.
    return f"""
OPTIONAL MATCH (existing {{id:$id}})
WITH collect(existing) AS existing
WHERE all(n IN existing WHERE n.tenant_id=$tenant_id
    AND n.projection=$projection AND '{label}' IN labels(n))
MERGE (n:{label} {{id:$id}})
ON CREATE SET n.tenant_id=$tenant_id, n.projection=$projection
WITH n WHERE n.tenant_id=$tenant_id AND n.projection=$projection
SET n.{name_field}=$name RETURN n.id
"""


PERSON = _node_write("Person", "display_name")
ORG = _node_write("Organization", "name")
LINK = """
OPTIONAL MATCH ()-[existing {id:$id}]->()
WITH collect(existing) AS existing
WHERE all(r IN existing WHERE r.tenant_id=$tenant_id
    AND r.projection=$projection AND type(r)='WORKS_AT')
MATCH (p:Person {id:$person_id, tenant_id:$tenant_id, projection:$projection})
MATCH (o:Organization {id:$organization_id, tenant_id:$tenant_id, projection:$projection})
MERGE (p)-[r:WORKS_AT {id:$id}]->(o)
ON CREATE SET r.tenant_id=$tenant_id, r.projection=$projection
WITH r WHERE r.tenant_id=$tenant_id AND r.projection=$projection
SET r.title=$title, r.role_type=$role_type, r.since=$since,
    r.until=null, r.confidence=$confidence
RETURN r.id
"""
PRUNE = """
MATCH (p:Person {id:$id, tenant_id:$tenant_id, projection:$projection})-[r:WORKS_AT]->()
WHERE r.tenant_id=$tenant_id AND r.projection=$projection AND NOT r.id IN $active
DELETE r
"""
PRUNE_ENDPOINT = """
MATCH (a)-[r:WORKS_AT {id:$id}]->(b)
WHERE r.tenant_id=$tenant_id AND r.projection=$projection
  AND (a.id<>$person_id OR b.id<>$organization_id)
DELETE r
"""
DELETE_NODE = """
MATCH (n {id:$id, tenant_id:$tenant_id, projection:$projection})
OPTIONAL MATCH (n)-[r]-()
WITH n,count(r) AS total,
 sum(CASE WHEN r.tenant_id=$tenant_id AND r.projection=$projection THEN 1 ELSE 0 END) AS owned
WHERE total=owned
WITH n,n.id AS deleted_id
DETACH DELETE n RETURN deleted_id
"""


async def _preflight(client, target, params, expected, role_ids=()):
    rows = (
        await client.query(
            target,
            "MATCH (n) WHERE n.id IN $ids RETURN n.id,n.tenant_id,n.projection,"
            "'Person' IN labels(n),'Organization' IN labels(n)",
            {"ids": list(expected)},
        )
    ).rows
    seen = set()
    for nid, tenant, marker, is_person, is_org in rows:
        if (
            nid in seen
            or tenant != params["tenant_id"]
            or marker != PROJECTION
            or not (is_person if expected[nid] == "Person" else is_org)
        ):
            raise ProjectionOwnershipError("node ownership collision")
        seen.add(nid)
    if role_ids:
        edges = (
            await client.query(
                target,
                "MATCH ()-[r]->() WHERE r.id IN $ids RETURN r.tenant_id,r.projection,type(r)",
                {"ids": list(role_ids)},
            )
        ).rows
        if any(
            t != params["tenant_id"] or m != PROJECTION or kind != "WORKS_AT"
            for t, m, kind in edges
        ):
            raise ProjectionOwnershipError("relationship ownership collision")
    return seen


async def _write_node(client, target, query, params):
    if len((await client.query(target, query, params)).rows) != 1:
        raise ProjectionOwnershipError("node write ownership changed")


async def _delete_node(client, target, params, label):
    present = await _preflight(client, target, params, {params["id"]: label})
    if not present:
        return
    # Check before any mutation; the write repeats the ownership condition
    # atomically so newly attached unrelated edges cannot be detached.
    edges = (
        await client.query(
            target, "MATCH (n {id:$id})-[r]-() RETURN r.tenant_id,r.projection", params
        )
    ).rows
    if any(t != params["tenant_id"] or m != PROJECTION for t, m in edges):
        raise ProjectionOwnershipError("unowned incident relationship")
    if len((await client.query(target, DELETE_NODE, params)).rows) != 1:
        raise ProjectionOwnershipError("deletion ownership changed")


async def _target(db, row):
    # A graph-name collision would defeat per-tenant routing. Refuse it before
    # any graph mutation, including source deletion cleanup.
    tenants = (
        (await db.execute(text("SELECT id,slug,graph_name,graph_mode FROM tenants")))
        .mappings()
        .all()
    )
    own = next((t for t in tenants if t["id"] == row.tenant_id), None)
    if own is None:
        return None
    route = own["graph_name"] or graph_name_for_slug(own["slug"])
    if route != row.graph_name:
        raise ValueError("tenant graph route changed; explicit reconciliation required")
    if any(
        t["id"] != row.tenant_id and (t["graph_name"] or graph_name_for_slug(t["slug"])) == route
        for t in tenants
    ):
        raise ValueError("tenant graph route is not exclusive")
    if own["graph_mode"] not in {"per_org_graph", "per_org_instance"}:
        raise ValueError("current projection requires an exclusive tenant graph")
    return TenantGraph(row.tenant_id, route, own["graph_mode"])


async def project_current(db: AsyncSession, client: FalkorDBGraphClient, row: GraphOutboxRow):
    target = await _target(db, row)
    if target is None:
        return
    params = {"id": str(row.entity_id), "tenant_id": str(row.tenant_id), "projection": PROJECTION}
    if row.entity_kind == "organization":
        org = (
            (
                await db.execute(
                    text("""
          SELECT display_name FROM organizations
          WHERE id=:id AND canonical_owner_tenant_id=:tid AND merge_status='canonical'
        """),
                    {"id": row.entity_id, "tid": row.tenant_id},
                )
            )
            .mappings()
            .first()
        )
        if org:
            await _preflight(client, target, params, {params["id"]: "Organization"})
            await _write_node(client, target, ORG, {**params, "name": org["display_name"]})
        else:
            await _delete_node(client, target, params, "Organization")
        return
    if row.entity_kind != "person":
        raise ValueError("unsupported current projection source")
    person = (
        (
            await db.execute(
                text("""
      SELECT display_name FROM persons
      WHERE id=:id AND canonical_owner_tenant_id=:tid AND merge_status='canonical'
    """),
                {"id": row.entity_id, "tid": row.tenant_id},
            )
        )
        .mappings()
        .first()
    )
    if person is None:
        await _delete_node(client, target, params, "Person")
        return
    # Match the supported active_only employment API: ended_at IS NULL.
    # Dates/equity/email/phone are not synthesized or exported as node metadata.
    links = (
        (
            await db.execute(
                text("""
      SELECT r.id,r.organization_id,r.title,r.role_type,r.started_at,r.confidence,o.display_name
      FROM person_org_role r JOIN organizations o ON o.id=r.organization_id
      JOIN persons p ON p.id=r.person_id
      WHERE r.person_id=:id AND r.ended_at IS NULL
        AND p.canonical_owner_tenant_id=:tid AND o.canonical_owner_tenant_id=:tid
        AND p.merge_status='canonical' AND o.merge_status='canonical'
      ORDER BY r.id
    """),
                {"id": row.entity_id, "tid": row.tenant_id},
            )
        )
        .mappings()
        .all()
    )
    expected = {params["id"]: "Person"}
    for link in links:
        oid = str(link["organization_id"])
        if oid in expected and expected[oid] != "Organization":
            raise ProjectionOwnershipError("source entity ID collision")
        expected[oid] = "Organization"
    await _preflight(client, target, params, expected, [str(x["id"]) for x in links])
    await _write_node(client, target, PERSON, {**params, "name": person["display_name"]})
    # Replay is a reconciliation, not an append: stale owned edges disappear.
    await client.query(target, PRUNE, {**params, "active": [str(x["id"]) for x in links]})
    for link in links:
        await _write_node(
            client,
            target,
            ORG,
            {**params, "id": str(link["organization_id"]), "name": link["display_name"]},
        )
        await client.query(
            target,
            PRUNE_ENDPOINT,
            {
                **params,
                "id": str(link["id"]),
                "person_id": str(row.entity_id),
                "organization_id": str(link["organization_id"]),
            },
        )
        result = await client.query(
            target,
            LINK,
            {
                **params,
                "id": str(link["id"]),
                "person_id": str(row.entity_id),
                "organization_id": str(link["organization_id"]),
                "title": link["title"],
                "role_type": link["role_type"],
                "since": link["started_at"].isoformat() if link["started_at"] else None,
                "confidence": float(link["confidence"] or 0),
            },
        )
        if len(result.rows) != 1:
            raise RuntimeError("current employment endpoints were not projected")


async def drain_current_projection(*, db, client=None, batch_size=100):
    from contact_ops.agents.graph_sync.falkordb_client import FalkorDBGraphClient
    from contact_ops.agents.graph_sync.worker import GraphOutboxRow, process_outbox_row

    acquired = await db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:lock)"), {"lock": DRAIN_LOCK}
    )
    if not acquired:
        await db.commit()
        return 0
    own_client = client is None
    graph_client = client or FalkorDBGraphClient()
    processed = 0
    try:
        rows = (
            (
                await db.execute(
                    text("""
          SELECT id,entity_kind,entity_id,op,payload,tenant_id,graph_name,attempts
          FROM graph_sync_outbox WHERE status='pending' AND payload->>'projection'=:projection
          ORDER BY created_at,id LIMIT :batch FOR UPDATE SKIP LOCKED
        """),
                    {"projection": PROJECTION, "batch": batch_size},
                )
            )
            .mappings()
            .all()
        )
        for entry in rows:
            row = GraphOutboxRow(**dict(entry))
            if await process_outbox_row(db=db, client=graph_client, row=row):
                processed += 1
        await db.commit()
        return processed
    finally:
        if own_client:
            await graph_client.close()
