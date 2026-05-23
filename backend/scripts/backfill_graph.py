"""One-shot FalkorDB backfill for a single Contact-Ops tenant."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from sqlalchemy import text

from contact_ops.agents.graph_sync.cypher_writes import build_write
from contact_ops.agents.graph_sync.falkordb_client import (
    FalkorDBGraphClient,
    TenantGraph,
    graph_name_for_slug,
)
from contact_ops.core.database import get_db_context


async def backfill_tenant(*, tenant_slug: str, dry_run: bool) -> dict[str, int]:
    async with get_db_context() as db:
        tenant_row = (
            await db.execute(
                text("SELECT id, slug, graph_name, graph_mode FROM tenants WHERE slug = :slug"),
                {"slug": tenant_slug},
            )
        ).mappings().first()
        if tenant_row is None:
            raise ValueError(f"tenant not found: {tenant_slug}")

        graph = TenantGraph(
            tenant_id=tenant_row["id"],
            graph_name=tenant_row["graph_name"] or graph_name_for_slug(tenant_row["slug"]),
            graph_mode=tenant_row["graph_mode"] or "per_org_graph",
        )
        client = FalkorDBGraphClient()
        try:
            if not dry_run:
                await client.bootstrap_graph(graph)
            counts = {
                "persons": await _backfill_rows(db, client, graph, "person", PERSON_SQL, dry_run),
                "organizations": await _backfill_rows(
                    db, client, graph, "organization", ORG_SQL, dry_run
                ),
                "person_org_role": await _backfill_rows(
                    db, client, graph, "edge:works_at", POR_SQL, dry_run
                ),
                "person_person_relation": await _backfill_rows(
                    db, client, graph, "edge:knows", PPR_SQL, dry_run
                ),
                "org_org_relation": await _backfill_rows(
                    db, client, graph, "edge:reports_to", OOR_SQL, dry_run
                ),
            }
            return counts
        finally:
            await client.close()


async def _backfill_rows(
    db: Any,
    client: FalkorDBGraphClient,
    graph: TenantGraph,
    entity_kind: str,
    sql: str,
    dry_run: bool,
) -> int:
    rows = (await db.execute(text(sql), {"tenant_id": graph.tenant_id})).mappings().all()
    if dry_run:
        return len(rows)
    for row in rows:
        payload = dict(row)
        write = build_write(entity_kind, "upsert", payload)
        await client.query(graph, write.cypher, write.params)
    return len(rows)


PERSON_SQL = """
SELECT id::text AS id, canonical_owner_tenant_id::text AS tenant_id, display_name,
       given_name, family_name, NULL::text AS primary_email, NULL::text AS primary_phone,
       NULL::text AS linkedin_url, updated_at::text AS updated_at,
       1.0::float AS confidence, NULL::text AS provenance_event_id
FROM persons
WHERE canonical_owner_tenant_id = :tenant_id
"""

ORG_SQL = """
SELECT id::text AS id, canonical_owner_tenant_id::text AS tenant_id, display_name AS name,
       legal_name, domain, updated_at::text AS updated_at,
       1.0::float AS confidence, NULL::text AS provenance_event_id
FROM organizations
WHERE canonical_owner_tenant_id = :tenant_id
"""

POR_SQL = """
SELECT por.id::text AS id, p.canonical_owner_tenant_id::text AS tenant_id,
       por.person_id::text AS person_id, por.organization_id::text AS organization_id,
       por.title, por.role_type::text AS role_type, por.started_at::text AS since,
       por.ended_at::text AS until, por.confidence::float AS confidence,
       NULL::text AS provenance_event_id
FROM person_org_role por
JOIN persons p ON p.id = por.person_id
WHERE p.canonical_owner_tenant_id = :tenant_id
"""

PPR_SQL = """
SELECT id::text AS id, tenant_visibility::text AS tenant_id,
       from_person_id::text, to_person_id::text, relation_type::text,
       inverse_relation_type::text, strength::float, started_at::text AS since,
       ended_at::text AS until,
       confidence::float, NULL::text AS provenance_event_id
FROM person_person_relation
WHERE tenant_visibility = :tenant_id
"""

OOR_SQL = """
SELECT id::text AS id, tenant_visibility::text AS tenant_id,
       from_org_id::text, to_org_id::text, relation_type::text,
       started_at::text AS since, ended_at::text AS until, confidence::float,
       NULL::text AS provenance_event_id
FROM org_org_relation
WHERE tenant_visibility = :tenant_id
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    counts = asyncio.run(backfill_tenant(tenant_slug=args.tenant, dry_run=args.dry_run))
    for name, count in counts.items():
        sys.stdout.write(f"{name}: {count}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
