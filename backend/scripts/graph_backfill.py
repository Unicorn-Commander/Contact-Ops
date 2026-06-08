"""One-off graph backfill: hydrate FalkorDB from the existing Postgres contacts.

Reads persons / organizations / employments / person-relations for the tenant
(RLS-bound) and writes them to the tenant's FalkorDB graph via the canonical
build_write templates, so the graph matches exactly what extract_ego_graph reads.
Idempotent (MERGE), safe to re-run.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from contact_ops.agents.graph_sync.cypher_writes import build_write
from contact_ops.agents.graph_sync.falkordb_client import FalkorDBGraphClient, TenantGraph
from contact_ops.core.config import get_settings
from contact_ops.core.database import async_session_maker, bind_session_context

# Configure per tenant; defaults are the dogfood (Magic Unicorn Inc.) tenant.
# Run in the backend container:  PYTHONPATH=/app python scripts/graph_backfill.py
TENANT = os.environ.get("BACKFILL_TENANT_ID", "019e50a3-d995-723f-ab66-0f765f92c0f4")
UC_UID = os.environ.get("BACKFILL_UC_UID", "REMOVED")
GRAPH = os.environ.get("BACKFILL_GRAPH_NAME", "contact_ops__magic_unicorn_inc")


def _iso(value):
    return value.isoformat() if value is not None else None


def _f(value, default=1.0):
    return float(value) if value is not None else default


async def main() -> None:
    settings = get_settings()
    client = FalkorDBGraphClient()
    tg = TenantGraph(tenant_id=uuid.UUID(TENANT), graph_name=GRAPH)
    now = datetime.now(UTC).isoformat()

    await client.bootstrap_graph(tg)  # id indexes (safe to repeat)

    async with async_session_maker() as db:
        await bind_session_context(db, TENANT, UC_UID, settings)

        persons = (
            await db.execute(
                text(
                    """
                    SELECT p.id, p.display_name, p.given_name, p.family_name,
                      (SELECT e.address FROM emails e WHERE e.person_id = p.id
                       ORDER BY e.is_primary DESC, e.created_at LIMIT 1) AS primary_email,
                      (SELECT ph.e164 FROM phones ph WHERE ph.person_id = p.id
                       ORDER BY ph.is_primary DESC, ph.created_at LIMIT 1) AS primary_phone
                    FROM persons p
                    """
                )
            )
        ).mappings().all()
        for r in persons:
            payload = {
                "id": str(r["id"]), "tenant_id": TENANT,
                "display_name": r["display_name"], "given_name": r["given_name"],
                "family_name": r["family_name"], "primary_email": r["primary_email"],
                "primary_phone": r["primary_phone"], "linkedin_url": None,
                "updated_at": now, "confidence": 1.0, "provenance_event_id": None,
            }
            w = build_write("person", "upsert", payload)
            await client.query(tg, w.cypher, w.params)

        orgs = (
            await db.execute(
                text("SELECT id, display_name, legal_name, domain FROM organizations")
            )
        ).mappings().all()
        for r in orgs:
            payload = {
                "id": str(r["id"]), "tenant_id": TENANT, "name": r["display_name"],
                "legal_name": r["legal_name"], "domain": r["domain"],
                "updated_at": now, "confidence": 1.0, "provenance_event_id": None,
            }
            w = build_write("organization", "upsert", payload)
            await client.query(tg, w.cypher, w.params)

        emps = (
            await db.execute(
                text(
                    """
                    SELECT id, person_id, organization_id, title, role_type,
                           started_at, ended_at, confidence
                    FROM person_org_role
                    """
                )
            )
        ).mappings().all()
        emp_ok = 0
        for r in emps:
            payload = {
                "id": str(r["id"]), "tenant_id": TENANT,
                "person_id": str(r["person_id"]), "organization_id": str(r["organization_id"]),
                "title": r["title"], "role_type": (str(r["role_type"]) if r["role_type"] else None),
                "since": _iso(r["started_at"]), "until": _iso(r["ended_at"]),
                "confidence": _f(r["confidence"]), "provenance_event_id": None,
            }
            w = build_write("edge:works_at", "upsert", payload)
            await client.query(tg, w.cypher, w.params)
            emp_ok += 1

        rels = (
            await db.execute(
                text(
                    """
                    SELECT id, from_person_id, to_person_id, relation_type,
                           inverse_relation_type, strength, started_at, ended_at, confidence
                    FROM person_person_relation
                    """
                )
            )
        ).mappings().all()
        for r in rels:
            payload = {
                "id": str(r["id"]), "tenant_id": TENANT,
                "from_person_id": str(r["from_person_id"]),
                "to_person_id": str(r["to_person_id"]),
                "relation_type": (str(r["relation_type"]) if r["relation_type"] else None),
                "inverse_relation_type": (
                    str(r["inverse_relation_type"]) if r["inverse_relation_type"] else None
                ),
                "strength": (_f(r["strength"], None) if r["strength"] is not None else None),
                "since": _iso(r["started_at"]), "until": _iso(r["ended_at"]),
                "confidence": _f(r["confidence"]), "provenance_event_id": None,
            }
            w = build_write("edge:knows", "upsert", payload)
            await client.query(tg, w.cypher, w.params)

    counts = await client.query(tg, "MATCH (n) RETURN count(n)", {})
    edges = await client.query(tg, "MATCH ()-[r]->() RETURN count(r)", {})
    await client.close()
    print(
        f"backfilled: persons={len(persons)} orgs={len(orgs)} "
        f"employments={emp_ok} relations={len(rels)}"
    )
    print(f"FalkorDB now: nodes={counts.rows} edges={edges.rows}")


asyncio.run(main())
