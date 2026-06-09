"""Enrichment MCP tools — derive structure from signals we already hold.

Sovereign-first: NO third-party enrichment APIs (Clearbit/Apollo/ZoomInfo etc.)
that would egress contacts. The first enrichment is purely local inference:

  ``infer_orgs_from_email_domains``
      Group people by the domain of their email address, skip free-mail/ISP
      domains, and propose a ``Works at`` edge to the matching organization —
      reusing an existing org (matched by domain, or by a normalized name key)
      or creating one named after the domain. dry_run defaults True (returns the
      plan); set dry_run=false to apply (audited; writes Postgres orgs +
      person_org_role edges and best-effort-syncs the new nodes/edges into the
      tenant's FalkorDB graph so they show up in the graph view immediately).

This fills both the relationship graph and the association filter, which are
otherwise almost entirely ``Works at`` ties seeded from explicit employment.
Inferred edges carry confidence 0.7 (an inference, not an assertion).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from contact_ops.agents.graph_sync.cypher_writes import build_write
from contact_ops.agents.graph_sync.falkordb_client import FalkorDBGraphClient
from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register
from contact_ops.mcp.tools.data_quality import _LEGAL_SUFFIXES
from contact_ops.mcp.tools.graph_admin import _tenant_graph
from contact_ops.models import Email, Organization, Person, PersonOrgRole
from contact_ops.models.enums import MergeStatus, OrgKind, RoleType

# ---- domain primitives --------------------------------------------------------

# Free-mail / ISP / disposable domains that are NOT employers — never inferred
# as an organization. Lowercase, registrable form.
_FREEMAIL: frozenset[str] = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com",
        "rocketmail.com", "hotmail.com", "hotmail.co.uk", "outlook.com",
        "live.com", "msn.com", "aol.com", "icloud.com", "me.com", "mac.com",
        "protonmail.com", "proton.me", "pm.me", "gmx.com", "gmx.net", "gmx.de",
        "mail.com", "zoho.com", "yandex.com", "yandex.ru", "fastmail.com",
        "hey.com", "duck.com", "hushmail.com", "tutanota.com", "tuta.io",
        "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "bellsouth.net",
        "cox.net", "charter.net", "earthlink.net", "frontier.com", "windstream.net",
        "optonline.net", "roadrunner.com", "rr.com", "juno.com", "netzero.net",
        "qq.com", "163.com", "126.com", "sina.com", "naver.com", "hanmail.net",
        "web.de", "t-online.de", "orange.fr", "free.fr", "laposte.net",
        "btinternet.com", "ntlworld.com", "sky.com", "virginmedia.com",
        "excite.com", "lycos.com", "angelfire.com", "geocities.com", "mail.ru",
        "inbox.com", "mailinator.com", "yopmail.com", "guerrillamail.com",
        "example.com", "example.org", "test.com", "email.com", "googlegroups.com",
    }
)

# Two-label public suffixes we must look *past* to find the registrable label
# (so acme.co.uk → "acme", not "co"). Not exhaustive — covers the common ones.
_MULTI_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.nz", "net.nz",
        "org.nz", "co.za", "com.br", "com.mx", "co.in", "co.jp", "com.sg",
        "com.hk", "com.tr", "co.kr", "com.cn", "co.il",
    }
)

_DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


def registrable_domain(host: str) -> str:
    """Collapse subdomains to the registrable domain so all of acme.com,
    mail.acme.com and us.acme.com key to one org. acme.co.uk is preserved."""
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_SUFFIXES:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def domain_of(address: str) -> str | None:
    """Lowercased registrable domain of an email address, or None if unusable.
    Subdomains are collapsed (dept.example.edu → example.edu) so a school/employer
    with many sub-hosts becomes ONE organization, not one per sub-host."""
    address = (address or "").strip().lower()
    if "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1].strip().strip(".")
    if not domain or " " in domain or not _DOMAIN_RE.match(domain):
        return None
    return registrable_domain(domain)


def registrable_label(domain: str) -> str:
    """The org-naming label of a domain: acme.com→acme, mail.acme.co.uk→acme."""
    parts = domain.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_SUFFIXES:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0]


def name_from_domain(domain: str) -> str:
    """A human-ish org name from a domain label (best-effort title-casing)."""
    label = registrable_label(domain).replace("-", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in label.split()) or domain


def name_key(value: str) -> str:
    """Normalized key for matching a domain label to an existing org name:
    lowercase alnum tokens minus legal suffixes, concatenated. So 'Harlow Bay
    LLC' and the domain 'janeroe.com' both key to 'janeroe'."""
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    kept = [t for t in tokens if t not in _LEGAL_SUFFIXES]
    return "".join(kept or tokens)


# ---- infer_orgs_from_email_domains --------------------------------------------


class InferOrgsInput(BaseModel):
    dry_run: bool = True
    # Minimum distinct people sharing a domain before we infer an employer from
    # it. 2 keeps it high-precision (a shared corporate domain); set 1 to be
    # aggressive. Linking to an ALREADY-EXISTING matched org ignores this floor.
    min_people: int = Field(default=2, ge=1, le=500)
    max_domains: int = Field(default=300, ge=1, le=2000)
    sample_limit: int = Field(default=25, ge=0, le=200)
    # Domains to skip entirely (e.g. classifieds relays, email-tracking
    # services, or anything the reviewer judges not-an-employer). Registrable
    # form, e.g. "craigslist.org". Applied on top of the built-in free-mail list.
    exclude_domains: list[str] = Field(default_factory=list)


class InferOrgsOutput(ToolOutput):
    dry_run: bool
    summary: dict[str, int]
    plan: list[dict[str, Any]]
    graph_synced: bool = False
    event_id: str | None = None


async def infer_orgs_from_email_domains(
    ctx: MCPContext, req: InferOrgsInput
) -> InferOrgsOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("org:write",))

    # 1) People in this tenant (RLS-scoped) → id→name.
    person_rows = (await ctx.db.execute(select(Person.id, Person.display_name))).all()
    person_name = {row[0]: row[1] for row in person_rows}
    if not person_name:
        return InferOrgsOutput(dry_run=req.dry_run, summary={}, plan=[])

    # 2) Emails → group person ids by registrable domain (skip free-mail).
    email_rows = (
        await ctx.db.execute(
            select(Email.person_id, Email.address).where(Email.person_id.isnot(None))
        )
    ).all()
    excluded = {d.strip().lower() for d in req.exclude_domains if d.strip()}
    domain_people: dict[str, set[uuid.UUID]] = {}
    for person_id, address in email_rows:
        if person_id not in person_name:
            continue  # cross-tenant guard
        domain = domain_of(address)
        if not domain or domain in _FREEMAIL or domain in excluded:
            continue
        domain_people.setdefault(domain, set()).add(person_id)

    # 3) Existing canonical orgs → match indexes (by domain, by name key).
    orgs = (
        (
            await ctx.db.execute(
                select(Organization).where(
                    Organization.canonical_owner_tenant_id == ctx.tenant_id,
                    Organization.merge_status == MergeStatus.canonical,
                )
            )
        )
        .scalars()
        .all()
    )
    by_domain: dict[str, Organization] = {}
    by_key: dict[str, Organization] = {}
    for org in orgs:
        if org.domain:
            by_domain.setdefault(org.domain.strip().lower(), org)
        by_key.setdefault(name_key(org.display_name), org)

    # 4) Existing employment edges → skip duplicates; people with any role.
    edge_rows = (
        await ctx.db.execute(select(PersonOrgRole.person_id, PersonOrgRole.organization_id))
    ).all()
    existing_edges = {(r[0], r[1]) for r in edge_rows}
    people_with_role = {r[0] for r in edge_rows}

    # 5) Build the plan, most-populated domains first.
    ordered = sorted(domain_people.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    plan: list[dict[str, Any]] = []
    new_org_count = existing_link_count = new_edge_count = 0
    affected_people: set[uuid.UUID] = set()

    for domain, people in ordered:
        existing = by_domain.get(domain) or by_key.get(name_key(registrable_label(domain)))
        # High-precision floor for NEW orgs; existing matches always link.
        if existing is None and len(people) < req.min_people:
            continue
        if len(plan) >= req.max_domains:
            break

        org_id = existing.id if existing else None
        new_edges = [pid for pid in people if (pid, org_id) not in existing_edges] if existing else list(people)
        if not new_edges:
            continue
        if existing is None:
            match_type = "new_org"
            org_name = name_from_domain(domain)
            new_org_count += 1
        elif by_domain.get(domain) is existing:
            match_type = "existing_by_domain"
            org_name = existing.display_name
            existing_link_count += 1
        else:
            match_type = "existing_by_name"
            org_name = existing.display_name
            existing_link_count += 1

        new_edge_count += len(new_edges)
        affected_people.update(new_edges)
        plan.append(
            {
                "domain": domain,
                "org_name": org_name,
                "org_id": str(org_id) if org_id else None,
                "match_type": match_type,
                "people_total": len(people),
                "new_edges": len(new_edges),
                "sample_people": [person_name[p] for p in new_edges[: req.sample_limit]]
                if req.sample_limit
                else [],
            }
        )

    summary = {
        "domains_in_plan": len(plan),
        "new_orgs": new_org_count,
        "existing_orgs_linked": existing_link_count,
        "new_works_at_edges": new_edge_count,
        "people_affected": len(affected_people),
        "free_mail_domains_skipped": sum(1 for d in domain_people if d in _FREEMAIL),
    }

    if req.dry_run or not plan:
        return InferOrgsOutput(dry_run=True, summary=summary, plan=plan)

    # 6) APPLY — create orgs + Works-at edges, then sync the new graph elements.
    now = datetime.now(UTC).isoformat()
    assigned_primary: set[uuid.UUID] = set()
    graph_orgs: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []

    for entry in plan:
        domain = entry["domain"]
        people = sorted(domain_people[domain])  # deterministic order
        if entry["org_id"]:
            org_id = uuid.UUID(entry["org_id"])
        else:
            org_id = uuid.uuid4()
            org = Organization(
                id=org_id,
                kind=OrgKind.company,
                legal_name=entry["org_name"],
                display_name=entry["org_name"],
                domain=domain,
                canonical_owner_tenant_id=ctx.tenant_id,
                merge_status=MergeStatus.canonical,
            )
            ctx.db.add(org)
            graph_orgs.append(
                {
                    "id": str(org_id), "tenant_id": str(ctx.tenant_id),
                    "name": entry["org_name"], "legal_name": entry["org_name"],
                    "domain": domain, "updated_at": now, "confidence": 1.0,
                    "provenance_event_id": None,
                }
            )
        for pid in people:
            if (pid, org_id) in existing_edges:
                continue
            # First inferred role for a person with NO employment becomes primary
            # (so their "current org" populates); others stay secondary.
            is_primary = pid not in people_with_role and pid not in assigned_primary
            if is_primary:
                assigned_primary.add(pid)
            role_id = uuid.uuid4()
            ctx.db.add(
                PersonOrgRole(
                    id=role_id,
                    person_id=pid,
                    organization_id=org_id,
                    role_type=RoleType.employee,
                    is_primary=is_primary,
                    confidence=0.7,
                )
            )
            existing_edges.add((pid, org_id))
            graph_edges.append(
                {
                    "id": str(role_id), "tenant_id": str(ctx.tenant_id),
                    "person_id": str(pid), "organization_id": str(org_id),
                    "title": None, "role_type": "employee", "since": None,
                    "until": None, "confidence": 0.7, "provenance_event_id": None,
                }
            )

    await ctx.db.flush()

    # Best-effort FalkorDB sync so the new orgs/edges appear in the graph view
    # right away. Never fails the apply (Postgres is the source of truth; a
    # re-run of the graph backfill would reconcile anyway).
    graph_synced = False
    try:
        client = FalkorDBGraphClient()
        try:
            tg = await _tenant_graph(ctx)
            await client.bootstrap_graph(tg)
            for payload in graph_orgs:
                w = build_write("organization", "upsert", payload)
                await client.query(tg, w.cypher, w.params)
            for payload in graph_edges:
                w = build_write("edge:works_at", "upsert", payload)
                await client.query(tg, w.cypher, w.params)
            graph_synced = True
        finally:
            await client.close()
    except Exception:  # noqa: BLE001 — graph sync is advisory, not load-bearing
        graph_synced = False

    event_id = await emit_action_event(
        ctx,
        event_type="organization.inferred_from_domains",
        aggregate_type="organization",
        aggregate_id=ctx.tenant_id,
        affected_ids=list(affected_people),
        payload_before=None,
        payload_after=summary,
        evidence={"plan": plan},
        confidence=0.7,
    )

    return InferOrgsOutput(
        dry_run=False,
        summary=summary,
        plan=plan,
        graph_synced=graph_synced,
        event_id=str(event_id) if event_id else None,
    )


def register_enrichment_tools() -> None:
    register(
        name="infer_orgs_from_email_domains",
        description=(
            "Sovereign enrichment: group people by their email DOMAIN (free-mail "
            "skipped), then propose a 'Works at' edge to a matching organization "
            "— reusing an org matched by domain/name or creating one named after "
            "the domain. dry_run defaults True (returns the plan + counts); set "
            "dry_run=false to apply (audited; writes orgs + employment edges and "
            "syncs them into the graph). Inferred edges carry confidence 0.7. "
            "Requires STAFF + org:write."
        ),
        input_model=InferOrgsInput,
        output_model=InferOrgsOutput,
        handler=infer_orgs_from_email_domains,
        required_role="STAFF",
        required_scopes=("org:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )


register_enrichment_tools()
