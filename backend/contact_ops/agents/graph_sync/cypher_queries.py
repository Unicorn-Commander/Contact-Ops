"""Parameterized read-query templates for graph MCP tools."""

SHORTEST_PATH = """
MATCH path = shortestPath((a {id: $from_person_id})-[*1..$max_hops]-(b {id: $to_person_id}))
RETURN path
LIMIT 1
"""

WHO_KNOWS = """
MATCH (p:Person)-[r:WORKS_AT]->(o:Organization {id: $organization_id})
WHERE ($role_filter IS NULL OR r.role_type = $role_filter OR r.title = $role_filter)
RETURN p.id, p.display_name, r.title, r.confidence
ORDER BY r.confidence DESC
LIMIT $limit
"""

MUTUAL_CONNECTIONS = """
MATCH (a:Person {id: $person_a_id})--(m:Person)--(b:Person {id: $person_b_id})
RETURN DISTINCT m.id, m.display_name
LIMIT $limit
"""

SUGGEST_INTRO = """
MATCH (from:Person {id: $from_person_id})-[r1]-(broker:Person)-[r2]-(target)
WHERE target.id = $target_id OR target.name = $target_label OR target.display_name = $target_label
RETURN broker.id, broker.display_name, r1.confidence, r2.confidence
ORDER BY (coalesce(r1.confidence, 0.5) + coalesce(r2.confidence, 0.5)) DESC
LIMIT $limit
"""

EGO_GRAPH = """
MATCH (f {id: $person_id})
OPTIONAL MATCH (f)-[r]-(n)
RETURN f.id,
       coalesce(f.display_name, f.name, f.address, f.e164),
       labels(f),
       n.id,
       coalesce(n.display_name, n.name, n.address, n.e164),
       labels(n),
       type(r),
       r.confidence
LIMIT $limit
"""

PATH_THROUGH_TOPIC = """
MATCH path = (p:Person {id: $person_id})-[*1..$max_hops]-(t:Topic {id: $topic_id})
RETURN path
LIMIT $limit
"""

DUPLICATES_GRAPH = """
MATCH (p:Person {id: $person_id})-[r:DUPLICATE_OF]-(d:Person)
RETURN DISTINCT d.id, d.display_name, r.confidence
ORDER BY r.confidence DESC
LIMIT $limit
"""
