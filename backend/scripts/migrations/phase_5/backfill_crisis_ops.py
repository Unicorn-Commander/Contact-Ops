"""Backfill opted-in Crisis-Ops people/organizations into Contact-Ops.

Phase 5.2. Crisis-Ops is a fixer/investigation platform; its ``entities`` table
holds people, organizations, evidence, events, legal, communications, and
locations, all strictly case-scoped. **Only ``people``/``organizations`` entity
*identity* crosses into Contact-Ops, and only when explicitly opted in.**
Everything else — every other entity type, every case-specific ``data`` key
(allegations, arrests, financials, ``aaron_*`` notes), and every
allegation/investigation relationship — stays local in Crisis-Ops, forever.
Identity relationships among linked entities are emitted as **propose-only**
descriptors; this script never auto-applies an edge.

``--dry-run`` is the DEFAULT and writes nothing. ``--apply`` is gated and must be
run by Aaron/the orchestrator with live credentials. The mapping in ``build_plan``
is pure and fixture-testable with no database.

See ``~/Documents/Contact-Ops-Phase-5.2-Crisis-Ops-Revised-Design.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from contact_ops.federation.consumer_sdk import ContactOpsConsumerClient

# --- The confidentiality contract, expressed as data ----------------------------

#: Only these two entity types are ever candidates for Contact-Ops.
LINKABLE_ENTITY_TYPES: frozenset[str] = frozenset({"people", "organizations"})

#: These entity types are never contacts and never leave the case.
LOCAL_ONLY_ENTITY_TYPES: frozenset[str] = frozenset(
    {"evidence", "events", "legal", "communication", "locations"}
)

#: Allowlist of ``data`` keys that count as identity. Anything not on this list
#: (allegation, arrest_*, financials, aaron_*, …) is dropped — the allowlist *is*
#: the privacy boundary, so unknown/case-specific keys never escape by default.
IDENTITY_DATA_KEYS: frozenset[str] = frozenset(
    {
        # person identity
        "email",
        "emails",
        "email_address",
        "phone",
        "phones",
        "phone_number",
        "mobile",
        "cell",
        "address",
        "addresses",
        "mailing_address",
        "street_address",
        "city",
        "state",
        "postal_code",
        "country",
        # organization identity
        "domain",
        "website",
        "legal_name",
    }
)

#: Belt-and-suspenders: even if one of these ever sneaks onto the identity
#: allowlist, a key whose lowercase form starts with one of these prefixes is
#: refused. (The allowlist already excludes them; this makes a mistake loud.)
FORBIDDEN_DATA_KEY_PREFIXES: tuple[str, ...] = (
    "allegation",
    "arrest",
    "aaron_",
    "financ",
    "amount",
    "misused",
    "investigat",
    "evidence",
)

#: Identity relationship types that may be *proposed* (never auto-applied).
#: Counsel edges are matched by substring (DEFENSE_COUNSEL_IN, PLAINTIFF_COUNSEL_FOR, …).
IDENTITY_RELATIONSHIP_TYPES: frozenset[str] = frozenset(
    {"WORKS_FOR", "SPOUSE_OF", "EQUITY_PARTNER_OF", "PARENT_OF", "WITNESS_IN"}
)

#: Allegation/investigation edges that never leave the case under any circumstance.
FORBIDDEN_RELATIONSHIP_TYPES: frozenset[str] = frozenset(
    {
        "CONSPIRED_WITH",
        "RETALIATED_AGAINST",
        "DISCRIMINATED_AGAINST",
        "PROVES",
        "VALIDATED",
        "LEGAL_PRECEDENT_MOST_ON_POINT",
    }
)

#: Best-effort suggested Contact-Ops relation for each identity edge. Advisory
#: only: edges are propose-only, so a human picks the final relation on apply.
CRISIS_TO_CONTACT_RELATION: dict[str, str] = {
    "SPOUSE_OF": "spouse_of",
    "PARENT_OF": "parent_of",
    "EQUITY_PARTNER_OF": "partner_of",
    "WITNESS_IN": "witness_for",
    "WORKS_FOR": "employment",  # person->org employment, not a person-person edge
}


def is_counsel_relation(relationship_type: str) -> bool:
    return "COUNSEL" in relationship_type.upper()


def is_identity_relationship(relationship_type: str) -> bool:
    upper = relationship_type.upper()
    return upper in IDENTITY_RELATIONSHIP_TYPES or is_counsel_relation(upper)


def suggested_relation(relationship_type: str) -> str:
    upper = relationship_type.upper()
    if is_counsel_relation(upper):
        return "counsel_for"
    return CRISIS_TO_CONTACT_RELATION.get(upper, "knows")


def extract_identity(data: dict[str, Any] | None) -> dict[str, Any]:
    """Return only the identity-allowlisted, non-forbidden keys from ``data``."""
    if not data:
        return {}
    identity: dict[str, Any] = {}
    for raw_key, value in data.items():
        key = str(raw_key).lower()
        if any(key.startswith(prefix) for prefix in FORBIDDEN_DATA_KEY_PREFIXES):
            continue
        if key in IDENTITY_DATA_KEYS:
            identity[key] = value
    return identity


# --- Source records --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrisisOpsEntity:
    case_slug: str
    entity_key: str
    entity_type: str
    name: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CrisisOpsRelationship:
    case_slug: str
    source_key: str
    target_key: str
    relationship_type: str
    attributes: dict[str, Any] = field(default_factory=dict)


def opt_in_key(case_slug: str, entity_key: str) -> str:
    return f"{case_slug}:{entity_key}"


def is_opted_in(entity: CrisisOpsEntity, opt_in: frozenset[str]) -> bool:
    """Opt-in is explicit and default-OFF (every link is human-confirmed)."""
    if entity.data.get("contact_ops_optin") is True:
        return True
    return (
        opt_in_key(entity.case_slug, entity.entity_key) in opt_in
        or entity.entity_key in opt_in
    )


# --- The plan --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinkItem:
    case_slug: str
    entity_key: str
    entity_type: str
    target_kind: str  # "person" | "org"
    name: str
    identity: dict[str, Any]
    source_identifier: str


@dataclass(frozen=True, slots=True)
class ProposedEdge:
    case_slug: str
    source_key: str
    target_key: str
    crisis_relationship_type: str
    suggested_relation: str
    propose_only: bool = True


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    tenant_id: uuid.UUID
    links: list[LinkItem] = field(default_factory=list)
    proposed_edges: list[ProposedEdge] = field(default_factory=list)
    excluded_entities: dict[str, int] = field(default_factory=dict)
    excluded_relationships: dict[str, int] = field(default_factory=dict)


def _bump(counter: dict[str, int], reason: str) -> None:
    counter[reason] = counter.get(reason, 0) + 1


def build_plan(
    entities: list[CrisisOpsEntity],
    relationships: list[CrisisOpsRelationship],
    *,
    tenant_id: uuid.UUID,
    opt_in: frozenset[str],
) -> BackfillPlan:
    """Pure mapping: decide exactly what (and only what) crosses to Contact-Ops."""
    links: list[LinkItem] = []
    excluded_entities: dict[str, int] = {}
    linked_keys: set[str] = set()

    for entity in entities:
        if entity.entity_type not in LINKABLE_ENTITY_TYPES:
            _bump(excluded_entities, f"non_contact_type:{entity.entity_type}")
            continue
        if not is_opted_in(entity, opt_in):
            _bump(excluded_entities, "not_opted_in")
            continue
        target_kind = "person" if entity.entity_type == "people" else "org"
        links.append(
            LinkItem(
                case_slug=entity.case_slug,
                entity_key=entity.entity_key,
                entity_type=entity.entity_type,
                target_kind=target_kind,
                name=entity.name,
                identity=extract_identity(entity.data),
                source_identifier=opt_in_key(entity.case_slug, entity.entity_key),
            )
        )
        linked_keys.add(opt_in_key(entity.case_slug, entity.entity_key))

    proposed_edges: list[ProposedEdge] = []
    excluded_relationships: dict[str, int] = {}
    for rel in relationships:
        source = opt_in_key(rel.case_slug, rel.source_key)
        target = opt_in_key(rel.case_slug, rel.target_key)
        rtype_upper = rel.relationship_type.upper()
        if rtype_upper in FORBIDDEN_RELATIONSHIP_TYPES:
            _bump(excluded_relationships, f"forbidden_edge:{rtype_upper}")
            continue
        if not is_identity_relationship(rel.relationship_type):
            _bump(excluded_relationships, f"non_identity_edge:{rtype_upper}")
            continue
        if source not in linked_keys or target not in linked_keys:
            _bump(excluded_relationships, "endpoint_not_linked")
            continue
        proposed_edges.append(
            ProposedEdge(
                case_slug=rel.case_slug,
                source_key=rel.source_key,
                target_key=rel.target_key,
                crisis_relationship_type=rel.relationship_type,
                suggested_relation=suggested_relation(rel.relationship_type),
            )
        )

    return BackfillPlan(
        tenant_id=tenant_id,
        links=links,
        proposed_edges=proposed_edges,
        excluded_entities=excluded_entities,
        excluded_relationships=excluded_relationships,
    )


def summarize_plan(plan: BackfillPlan) -> dict[str, Any]:
    return {
        "source": "crisis-ops",
        "tenant_id": str(plan.tenant_id),
        "links_total": len(plan.links),
        "links_people": sum(1 for link in plan.links if link.target_kind == "person"),
        "links_orgs": sum(1 for link in plan.links if link.target_kind == "org"),
        "proposed_edges_total": len(plan.proposed_edges),
        "proposed_edges_all_propose_only": all(edge.propose_only for edge in plan.proposed_edges),
        "excluded_entities": dict(sorted(plan.excluded_entities.items())),
        "excluded_relationships": dict(sorted(plan.excluded_relationships.items())),
        "links": [
            {
                "case_slug": link.case_slug,
                "entity_key": link.entity_key,
                "entity_type": link.entity_type,
                "target_kind": link.target_kind,
                "name": link.name,
                "identity_keys": sorted(link.identity.keys()),
                "source_identifier": link.source_identifier,
                "link_state": "proposed",
            }
            for link in plan.links
        ],
        "proposed_edges": [
            {
                "case_slug": edge.case_slug,
                "source_key": edge.source_key,
                "target_key": edge.target_key,
                "crisis_relationship_type": edge.crisis_relationship_type,
                "suggested_relation": edge.suggested_relation,
                "propose_only": edge.propose_only,
                "applied": False,
            }
            for edge in plan.proposed_edges
        ],
    }


# --- Source loaders (live: only used under --apply / explicit --source-db) -------


def _asyncpg_url(url: str) -> str:
    if url.startswith("postgresql+"):
        return url
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def load_fixture(path: str) -> tuple[list[CrisisOpsEntity], list[CrisisOpsRelationship]]:
    """Load entities/relationships from a JSON fixture (no database)."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    entities = [
        CrisisOpsEntity(
            case_slug=str(row["case_slug"]),
            entity_key=str(row["entity_key"]),
            entity_type=str(row["entity_type"]),
            name=str(row.get("name", "")),
            data=dict(row.get("data") or {}),
        )
        for row in payload.get("entities", [])
    ]
    relationships = [
        CrisisOpsRelationship(
            case_slug=str(row["case_slug"]),
            source_key=str(row["source_key"]),
            target_key=str(row["target_key"]),
            relationship_type=str(row["relationship_type"]),
            attributes=dict(row.get("attributes") or {}),
        )
        for row in payload.get("relationships", [])
    ]
    return entities, relationships


async def load_from_source_db(
    source_db: str,
) -> tuple[list[CrisisOpsEntity], list[CrisisOpsRelationship]]:
    engine = create_async_engine(_asyncpg_url(source_db), pool_pre_ping=True)
    try:
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            entity_rows = await session.execute(
                text(
                    """
                    SELECT c.code AS case_slug, e.entity_key, e.entity_type, e.name, e.data
                    FROM entities e
                    JOIN cases c ON c.id = e.case_id
                    ORDER BY c.code, e.entity_key
                    """
                )
            )
            entities = [
                CrisisOpsEntity(
                    case_slug=str(row["case_slug"]),
                    entity_key=str(row["entity_key"]),
                    entity_type=str(row["entity_type"]),
                    name=str(row["name"] or ""),
                    data=dict(row["data"] or {}),
                )
                for row in entity_rows.mappings().all()
            ]
            rel_rows = await session.execute(
                text(
                    """
                    SELECT c.code AS case_slug, r.source_key, r.target_key,
                           r.relationship_type, r.attributes
                    FROM relationships r
                    JOIN cases c ON c.id = r.case_id
                    ORDER BY c.code, r.source_key, r.target_key
                    """
                )
            )
            relationships = [
                CrisisOpsRelationship(
                    case_slug=str(row["case_slug"]),
                    source_key=str(row["source_key"]),
                    target_key=str(row["target_key"]),
                    relationship_type=str(row["relationship_type"]),
                    attributes=dict(row["attributes"] or {}),
                )
                for row in rel_rows.mappings().all()
            ]
            return entities, relationships
    finally:
        await engine.dispose()


# --- Apply (gated; identity links only — edges stay propose-only descriptors) ----


def _identity_emails(identity: dict[str, Any]) -> list[dict[str, Any]]:
    raw = identity.get("email") or identity.get("emails") or identity.get("email_address")
    values = raw if isinstance(raw, list) else [raw] if raw else []
    return [
        {"address": str(value), "type": "work", "is_primary": index == 0}
        for index, value in enumerate(values)
        if value
    ]


def _identity_phones(identity: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (
        identity.get("phone")
        or identity.get("phones")
        or identity.get("phone_number")
        or identity.get("mobile")
        or identity.get("cell")
    )
    values = raw if isinstance(raw, list) else [raw] if raw else []
    return [
        {"e164": str(value), "type": "work", "is_primary": index == 0}
        for index, value in enumerate(values)
        if value
    ]


async def _record_entity_link(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    link: LinkItem,
    person_id: uuid.UUID | None,
    org_id: uuid.UUID | None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO crisis_ops_entity_link (
                case_slug, entity_key, entity_type,
                contact_ops_person_id, contact_ops_org_id,
                link_state, tenant_id, last_synced_at
            )
            VALUES (
                :case_slug, :entity_key, :entity_type,
                :person_id, :org_id,
                'proposed', :tenant_id, now()
            )
            ON CONFLICT (case_slug, entity_key) DO UPDATE
            SET contact_ops_person_id = EXCLUDED.contact_ops_person_id,
                contact_ops_org_id = EXCLUDED.contact_ops_org_id,
                entity_type = EXCLUDED.entity_type,
                last_synced_at = now()
            """
        ),
        {
            "case_slug": link.case_slug,
            "entity_key": link.entity_key,
            "entity_type": link.entity_type,
            "person_id": person_id,
            "org_id": org_id,
            "tenant_id": tenant_id,
        },
    )


async def apply_plan(
    plan: BackfillPlan,
    *,
    contact_ops_db: str,
    subject_token: str,
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    mcp_url: str,
) -> dict[str, Any]:
    """Gated. Creates/links identity only. Never applies a relationship edge."""
    engine = create_async_engine(_asyncpg_url(contact_ops_db), pool_pre_ping=True)
    client = ContactOpsConsumerClient(
        token_endpoint=token_endpoint,
        client_id=client_id,
        client_secret=client_secret,
        mcp_url=mcp_url,
        scope="contactops:contacts.read contactops:contacts.write",
    )
    namespace = "crisis-ops:entity"
    people_created = orgs_created = linked = 0
    try:
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(plan.tenant_id)},
            )
            for link in plan.links:
                idempotency = uuid.uuid5(uuid.NAMESPACE_URL, f"crisis-ops:{link.source_identifier}")
                person_id: uuid.UUID | None = None
                org_id: uuid.UUID | None = None
                if link.target_kind == "person":
                    existing = await client.find_person_by_identifier(
                        subject_token=subject_token,
                        namespace=namespace,
                        value=link.source_identifier,
                    )
                    if existing is None:
                        result = await client.call_tool(
                            subject_token=subject_token,
                            tool="create_person",
                            arguments={
                                "display_name": link.name,
                                "emails": _identity_emails(link.identity),
                                "phones": _identity_phones(link.identity),
                                "initial_tags": ["crisis-ops", f"crisis-ops:{link.case_slug}"],
                                "idempotency_key": str(idempotency),
                                "confidence": 1.0,
                            },
                        )
                        person_id = uuid.UUID(str(result["person_id"]))
                        people_created += 1
                        await client.call_tool(
                            subject_token=subject_token,
                            tool="add_identifier",
                            arguments={
                                "subject_kind": "person",
                                "subject_id": str(person_id),
                                "namespace": namespace,
                                "value": link.source_identifier,
                                "verified": True,
                                "confidence": 1.0,
                            },
                        )
                    else:
                        person_id = existing.person_id
                else:
                    found = await client.call_tool(
                        subject_token=subject_token,
                        tool="find_organization_by_identifier",
                        arguments={
                            "identifiers": [
                                {"namespace": namespace, "value": link.source_identifier}
                            ]
                        },
                    )
                    matches = found.get("matches") or []
                    if matches:
                        org_id = uuid.UUID(str(matches[0]["org_id"]))
                    else:
                        result = await client.call_tool(
                            subject_token=subject_token,
                            tool="create_organization",
                            arguments={
                                "legal_name": str(link.identity.get("legal_name") or link.name),
                                "display_name": link.name,
                                "domain": link.identity.get("domain"),
                                "idempotency_key": str(idempotency),
                            },
                        )
                        org_id = uuid.UUID(str(result["org_id"]))
                        orgs_created += 1
                await _record_entity_link(
                    session,
                    tenant_id=plan.tenant_id,
                    link=link,
                    person_id=person_id,
                    org_id=org_id,
                )
                linked += 1
            await session.commit()
    finally:
        await client.close()
        await engine.dispose()
    return {
        "people_created": people_created,
        "orgs_created": orgs_created,
        "entity_links_written": linked,
        # Edges are NEVER auto-applied; they remain propose-only for human review.
        "edges_applied": 0,
        "edges_proposed_for_review": len(plan.proposed_edges),
    }


# --- CLI -------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", help="JSON fixture of entities/relationships (no DB)")
    parser.add_argument("--source-db", help="Crisis-Ops crisis_ops DB URL (live read; gated)")
    parser.add_argument("--tenant-id", required=True, help="Contact-Ops tenant UUID")
    parser.add_argument(
        "--opt-in",
        default="",
        help="Comma-separated opted-in keys ('case:entity_key' or 'entity_key').",
    )
    parser.add_argument("--opt-in-file", help="Newline-delimited opt-in keys file")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="GATED: perform live writes. Default is dry-run (writes nothing).",
    )
    parser.add_argument("--contact-ops-db")
    parser.add_argument("--subject-token")
    parser.add_argument("--token-endpoint")
    parser.add_argument("--client-id", default="crisis-ops-contactops-consumer")
    parser.add_argument("--client-secret")
    parser.add_argument("--mcp-url", default="https://mcp.contacts.magicunicorn.dev")
    return parser


def _resolve_opt_in(args: argparse.Namespace) -> frozenset[str]:
    keys: set[str] = {key.strip() for key in str(args.opt_in).split(",") if key.strip()}
    if args.opt_in_file:
        with open(args.opt_in_file, encoding="utf-8") as handle:
            keys.update(line.strip() for line in handle if line.strip())
    return frozenset(keys)


async def _main() -> int:
    args = _parser().parse_args()
    tenant_id = uuid.UUID(str(args.tenant_id))
    opt_in = _resolve_opt_in(args)

    if args.fixture:
        entities, relationships = load_fixture(str(args.fixture))
    elif args.source_db:
        entities, relationships = await load_from_source_db(str(args.source_db))
    else:
        sys.stderr.write("provide --fixture (dry-run) or --source-db (live)\n")
        return 2

    plan = build_plan(entities, relationships, tenant_id=tenant_id, opt_in=opt_in)
    summary = summarize_plan(plan)

    if not args.apply:
        summary["mode"] = "dry-run"
        sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        return 0

    required = {
        "source_db": args.source_db,
        "contact_ops_db": args.contact_ops_db,
        "subject_token": args.subject_token,
        "token_endpoint": args.token_endpoint,
        "client_secret": args.client_secret,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        sys.stderr.write(f"missing required --apply options: {', '.join(missing)}\n")
        return 2

    result = await apply_plan(
        plan,
        contact_ops_db=str(args.contact_ops_db),
        subject_token=str(args.subject_token),
        token_endpoint=str(args.token_endpoint),
        client_id=str(args.client_id),
        client_secret=str(args.client_secret),
        mcp_url=str(args.mcp_url),
    )
    summary["mode"] = "apply"
    summary["apply"] = result
    sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
