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
