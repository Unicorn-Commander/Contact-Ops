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

PERSON = """
MERGE (p:Person {id:$id, tenant_id:$tenant_id})
SET p.display_name=$name, p.projection=$projection
RETURN p.id
"""
ORG = """
MERGE (o:Organization {id:$id, tenant_id:$tenant_id})
SET o.name=$name, o.projection=$projection
RETURN o.id
"""
LINK = """
MATCH (p:Person {id:$person_id, tenant_id:$tenant_id})
MATCH (o:Organization {id:$organization_id, tenant_id:$tenant_id})
MERGE (p)-[r:WORKS_AT {id:$id}]->(o)
SET r.tenant_id=$tenant_id, r.projection=$projection, r.title=$title,
    r.role_type=$role_type, r.since=$since, r.until=null, r.confidence=$confidence
RETURN r.id
"""
PRUNE = """
MATCH (p:Person {id:$id, tenant_id:$tenant_id})-[r:WORKS_AT]->()
WHERE r.tenant_id=$tenant_id AND r.projection=$projection AND NOT r.id IN $active
DELETE r
"""
DELETE_PERSON = """
MATCH (p:Person {id:$id, tenant_id:$tenant_id, projection:$projection})
DETACH DELETE p
"""
DELETE_ORG = """
MATCH (o:Organization {id:$id, tenant_id:$tenant_id, projection:$projection})
DETACH DELETE o
"""


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
        await client.query(
            target,
            ORG if org else DELETE_ORG,
            {**params, "name": org["display_name"]} if org else params,
        )
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
        await client.query(target, DELETE_PERSON, params)
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
    await client.query(target, PERSON, {**params, "name": person["display_name"]})
    # Replay is a reconciliation, not an append: stale owned edges disappear.
    await client.query(target, PRUNE, {**params, "active": [str(x["id"]) for x in links]})
    for link in links:
        await client.query(
            target,
            ORG,
            {**params, "id": str(link["organization_id"]), "name": link["display_name"]},
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
        if not result.rows:
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
