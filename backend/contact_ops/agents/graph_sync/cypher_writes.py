"""Idempotent Cypher write templates for graph_sync_outbox rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CypherWrite:
    cypher: str
    params: dict[str, Any]


PERSON_UPSERT = """
MERGE (p:Person {id: $id})
SET p.tenant_id = $tenant_id,
    p.display_name = $display_name,
    p.given_name = $given_name,
    p.family_name = $family_name,
    p.primary_email = $primary_email,
    p.primary_phone = $primary_phone,
    p.linkedin_url = $linkedin_url,
    p.updated_at = $updated_at,
    p.confidence = $confidence,
    p.provenance_event_id = $provenance_event_id
RETURN p.id
"""

ORGANIZATION_UPSERT = """
MERGE (o:Organization {id: $id})
SET o.tenant_id = $tenant_id,
    o.name = $name,
    o.legal_name = $legal_name,
    o.domain = $domain,
    o.updated_at = $updated_at,
    o.confidence = $confidence,
    o.provenance_event_id = $provenance_event_id
RETURN o.id
"""

WORKS_AT_UPSERT = """
MATCH (p:Person {id: $person_id})
MATCH (o:Organization {id: $organization_id})
MERGE (p)-[r:WORKS_AT {id: $id}]->(o)
SET r.tenant_id = $tenant_id,
    r.title = $title,
    r.role_type = $role_type,
    r.since = $since,
    r.until = $until,
    r.confidence = $confidence,
    r.provenance_event_id = $provenance_event_id
RETURN r.id
"""

PERSON_RELATION_UPSERT = """
MATCH (a:Person {id: $from_person_id})
MATCH (b:Person {id: $to_person_id})
MERGE (a)-[r:KNOWS {id: $id}]->(b)
SET r.tenant_id = $tenant_id,
    r.kind = $relation_type,
    r.inverse_kind = $inverse_relation_type,
    r.strength = $strength,
    r.since = $since,
    r.until = $until,
    r.confidence = $confidence,
    r.provenance_event_id = $provenance_event_id
RETURN r.id
"""

ORG_RELATION_UPSERT = """
MATCH (a:Organization {id: $from_org_id})
MATCH (b:Organization {id: $to_org_id})
MERGE (a)-[r:REPORTS_TO {id: $id}]->(b)
SET r.tenant_id = $tenant_id,
    r.kind = $relation_type,
    r.since = $since,
    r.until = $until,
    r.confidence = $confidence,
    r.provenance_event_id = $provenance_event_id
RETURN r.id
"""

EMAIL_UPSERT = """
MERGE (e:Email {id: $id})
SET e.tenant_id = $tenant_id,
    e.address = $address,
    e.updated_at = $updated_at,
    e.confidence = $confidence,
    e.provenance_event_id = $provenance_event_id
WITH e
MATCH (p:Person {id: $person_id})
MERGE (p)-[r:HAS_EMAIL {id: $edge_id}]->(e)
SET r.tenant_id = $tenant_id,
    r.confidence = $confidence,
    r.provenance_event_id = $provenance_event_id
RETURN e.id
"""

PHONE_UPSERT = """
MERGE (ph:Phone {id: $id})
SET ph.tenant_id = $tenant_id,
    ph.e164 = $e164,
    ph.updated_at = $updated_at,
    ph.confidence = $confidence,
    ph.provenance_event_id = $provenance_event_id
WITH ph
MATCH (p:Person {id: $person_id})
MERGE (p)-[r:HAS_PHONE {id: $edge_id}]->(ph)
SET r.tenant_id = $tenant_id,
    r.confidence = $confidence,
    r.provenance_event_id = $provenance_event_id
RETURN ph.id
"""

TAGGED_UPSERT = """
MERGE (t:Tag {id: $tag_id})
SET t.tenant_id = $tenant_id,
    t.name = $tag_name,
    t.updated_at = $updated_at
WITH t
MATCH (n {id: $entity_id})
MERGE (n)-[r:TAGGED {id: $id}]->(t)
SET r.tenant_id = $tenant_id,
    r.confidence = $confidence,
    r.provenance_event_id = $provenance_event_id
RETURN r.id
"""

DELETE_ENTITY = """
MATCH (n {id: $id})
SET n.deleted_at = $deleted_at,
    n.updated_at = $updated_at
RETURN n.id
"""


def build_write(entity_kind: str, op: str, payload: dict[str, Any]) -> CypherWrite:
    if op == "delete":
        return CypherWrite(DELETE_ENTITY, payload)
    templates = {
        "person": PERSON_UPSERT,
        "organization": ORGANIZATION_UPSERT,
        "edge:works_at": WORKS_AT_UPSERT,
        "edge:has_email": EMAIL_UPSERT,
        "edge:has_phone": PHONE_UPSERT,
        "edge:knows": PERSON_RELATION_UPSERT,
        "edge:family_of": PERSON_RELATION_UPSERT,
        "edge:reports_to": ORG_RELATION_UPSERT,
        "edge:counsel_for": PERSON_RELATION_UPSERT,
        "edge:witness_for": PERSON_RELATION_UPSERT,
        "edge:party_to": PERSON_RELATION_UPSERT,
        "edge:duplicate_of": PERSON_RELATION_UPSERT,
        "field_provenance": TAGGED_UPSERT,
    }
    try:
        return CypherWrite(templates[entity_kind], payload)
    except KeyError as exc:
        raise ValueError(f"unsupported graph outbox entity_kind: {entity_kind}") from exc


# ---- event_outbox edge-sync channels --------------------------------------------
# The dedup merge tools (mcp/tools/data_quality.py) and the voice-match resolver
# (agents/voice_match.py, mcp/tools/voice_match_admin.py) publish edge operations
# to ``event_outbox`` — not ``graph_sync_outbox`` — under two channels. These build
# the Cypher the drain applies for each. Every write is a MERGE/DELETE so replaying
# a claimed-but-unmarked row is a no-op (idempotent).

_DEDUP_ENTITY_LABELS = {"person": "Person", "organization": "Organization"}


def _dedup_alias_edge_write(payload: dict[str, Any]) -> CypherWrite:
    """Translate a ``falkordb.edge.create`` dedup payload into an alias-edge write.

    Payload shape (see ``data_quality._enqueue_{person,org}_graph_sync``)::

        {"type": <op>, "entity": "person"|"organization",
         "survivor_id": ..., "alias_id": ..., "edge_label": "dedup:alias_of"}

    A ``*_merge_applied`` op MERGEs an ``ALIAS_OF`` edge from the loser to the
    survivor; a ``*_unmerge`` op deletes it.
    """
    entity = str(payload.get("entity") or "")
    label = _DEDUP_ENTITY_LABELS.get(entity)
    if label is None:
        raise ValueError(f"unsupported dedup edge entity: {entity!r}")
    op_type = str(payload.get("type") or "")
    params = {
        "survivor_id": str(payload["survivor_id"]),
        "alias_id": str(payload["alias_id"]),
        "edge_label": payload.get("edge_label"),
    }
    # ``label`` is whitelisted above, so the inline interpolation is injection-safe;
    # ids + edge_label ride through as bound Cypher params.
    if op_type.endswith("unmerge"):
        cypher = (
            f"MATCH (alias:{label} {{id: $alias_id}})"
            f"-[r:ALIAS_OF]->(survivor:{label} {{id: $survivor_id}}) "
            f"DELETE r"
        )
    else:
        cypher = (
            f"MATCH (survivor:{label} {{id: $survivor_id}}) "
            f"MATCH (alias:{label} {{id: $alias_id}}) "
            f"MERGE (alias)-[r:ALIAS_OF]->(survivor) "
            f"SET r.edge_label = $edge_label"
        )
    return CypherWrite(cypher, params)


def build_edge_event_write(channel: str, payload: dict[str, Any]) -> CypherWrite:
    """Build the Cypher write for an ``event_outbox`` edge-sync row.

    ``graph.sync_edge`` rows carry a ready ``cypher_template`` + ``params`` (the
    voice-match resolver already writes Cypher); ``falkordb.edge.create`` rows are
    dedup alias edges translated by ``_dedup_alias_edge_write``.
    """
    if channel == "graph.sync_edge":
        cypher = payload.get("cypher_template")
        if not cypher:
            raise ValueError("graph.sync_edge payload missing cypher_template")
        return CypherWrite(str(cypher), dict(payload.get("params") or {}))
    if channel == "falkordb.edge.create":
        return _dedup_alias_edge_write(payload)
    raise ValueError(f"unsupported edge-sync channel: {channel!r}")
