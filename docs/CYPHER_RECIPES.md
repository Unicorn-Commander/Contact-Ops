# Contact-Ops Cypher Recipes

All examples run against one tenant graph, for example `contact_ops__aaron_personal`.
Never run cross-tenant graph queries by default; enumerate every target graph only
from an ADMIN-gated operator path.

## Who do I know at Summit Medical?

```cypher
MATCH (p:Person)-[r:WORKS_AT]->(o:Organization {id: $summit medical_org_id})
RETURN p.id, p.display_name, r.title, r.confidence
ORDER BY r.confidence DESC
LIMIT 50
```

## Shortest path from Aaron to Leo Grant

```cypher
MATCH path = shortestPath((a:Person {id: $aaron_person_id})-[*1..5]-(i:Person {id: $isaac_chan_person_id}))
RETURN path
LIMIT 1
```

## Second-degree people who care about FERS disability

```cypher
MATCH (me:Person {id: $aaron_person_id})-[*1..2]-(p:Person)-[:TAGGED]->(t:Tag {name: $fers_disability_tag})
RETURN DISTINCT p.id, p.display_name
LIMIT 100
```

## Sullivan case witnesses connected to FBDPS people

```cypher
MATCH (w:Person)-[:WITNESS_FOR]->(case_tag:Tag {name: $sullivan_case_tag})
MATCH (w)-[*1..3]-(fbdps:Person)-[:WORKS_AT]->(o:Organization {id: $fbdps_org_id})
RETURN DISTINCT w.id, w.display_name, fbdps.id, fbdps.display_name
LIMIT 100
```

## Duplicate chain for a person

```cypher
MATCH (p:Person {id: $person_id})-[:DUPLICATE_OF*1..4]-(candidate:Person)
RETURN DISTINCT candidate.id, candidate.display_name
LIMIT 50
```

## Counsel and parties around one legal matter

```cypher
MATCH (p:Person)-[r]-(matter:Tag {id: $matter_tag_id})
WHERE type(r) IN ['COUNSEL_FOR', 'PARTY_TO', 'WITNESS_FOR']
RETURN p.id, p.display_name, type(r), r.confidence
ORDER BY type(r), p.display_name
```
