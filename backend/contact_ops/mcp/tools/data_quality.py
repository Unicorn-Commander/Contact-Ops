"""Data-quality MCP tools — junk-value detection + safe normalizations.

This module surfaces the dirty-data classes a real import leaves behind and
offers *reversible, audited, dry-run-by-default* fixes for the unambiguous
ones. It deliberately does NOT do anything destructive on its own:

  - ``data_quality_report``  read-only scan (orgs + tags).
  - ``clean_org_names``      strip literal wrapping quotes + collapse
                             whitespace on org names. dry_run defaults True.
  - ``canonicalize_tags``    casefold/slugify tag variants so ``Folly Beach``
                             and ``folly-beach`` collapse. dry_run True.

All three run under scopes the owner already holds (org:read / org:write /
tag:read / tag:write) — no new Keycloak scope is introduced, so there is no
"scope seeded nowhere -> 403" trap.

Normalization is intentionally conservative and deterministic (no fuzzy ML,
no new heavy dependency): wrapping-quote strip, whitespace collapse, and a
legal-suffix-aware canonical key for *grouping* duplicate candidates. The
actual org merge lives in a separate, human-gated tool.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import ORG_NOT_FOUND, VALIDATION_FAILED, ToolError
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register
from contact_ops.models import Organization, Person, PersonOrgRole, PersonTenantMembership
from contact_ops.models.email import Email
from contact_ops.models.enums import MergeStatus

# ---- normalization primitives -------------------------------------------------

# Quote pairs we strip when they *wrap* a whole name (ASCII + curly + backtick).
_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ("'", "'"),
    ('"', '"'),
    ("‘", "’"),  # ‘ ’
    ("“", "”"),  # “ ”
    ("`", "`"),
)

# Legal-form tokens dropped when building the canonical *grouping* key so that
# "Harlow Bay", "Harlow Bay LLC" and "HARLOW BAY L.L.C." land together.
_LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "llp",
        "ltd",
        "limited",
        "co",
        "corp",
        "corporation",
        "company",
        "plc",
        "pllc",
        "pc",
        "pa",
        "lp",
        "gmbh",
        "ag",
        "sa",
        "nv",
        "bv",
        "group",
        "holdings",
        "intl",
        "international",
    }
)


def strip_wrapping_quotes(name: str) -> str:
    """Remove one or more layers of matching wrapping quotes: ``'X'`` -> ``X``."""
    s = name.strip()
    changed = True
    while changed and len(s) >= 2:
        changed = False
        for lo, hi in _QUOTE_PAIRS:
            if s[0] == lo and s[-1] == hi and len(s) >= 2:
                s = s[1:-1].strip()
                changed = True
                break
    return s


def clean_display(name: str) -> str:
    """Display-safe cleanup: strip wrapping quotes, collapse inner whitespace."""
    s = strip_wrapping_quotes(name)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_phone_like(name: str) -> bool:
    """True when a name is just a phone number (digits, no letters)."""
    stripped = strip_wrapping_quotes(name)
    digits = re.sub(r"\D", "", stripped)
    letters = re.sub(r"[^A-Za-z]", "", stripped)
    return len(digits) >= 7 and len(letters) == 0


# Singular role/title nouns that almost never END a real company name — used to
# flag a job title parsed into the org field (e.g. "GenX Dayshift Manager").
# Companies that genuinely contain these usually pluralize ("Acme Engineers") or
# carry a legal/company marker, both of which are excluded below.
_TITLE_LAST_WORDS: frozenset[str] = frozenset(
    {
        "manager", "director", "supervisor", "coordinator", "clerk", "cashier",
        "receptionist", "secretary", "technician", "operator", "foreman",
        "intern", "president", "ceo", "cfo", "cto", "coo", "officer", "owner",
        "administrator", "bookkeeper", "dispatcher", "custodian", "janitor",
        "driver", "bartender", "server", "cook", "chef", "nurse", "teacher",
        "professor", "assistant", "representative", "consultant", "analyst",
        "engineer", "specialist", "agent", "associate",
    }
)


def looks_like_job_title(name: str) -> bool:
    """Conservative heuristic: a multi-word name whose LAST word is a singular
    role noun and that carries no company marker ('&' or a legal suffix) — i.e. a
    job title that landed in the org field. Read-only (surfaced for review)."""
    s = clean_display(name)
    if not s or "&" in s:
        return False
    tokens = re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split()
    if len(tokens) < 2 or any(t in _LEGAL_SUFFIXES for t in tokens):
        return False
    return tokens[-1] in _TITLE_LAST_WORDS


def dedup_key(name: str) -> str:
    """Aggressive canonical key for grouping duplicate-candidate org names.

    Lowercase, drop dots (so ``L.L.C.`` -> ``llc``), collapse remaining
    punctuation to spaces, drop trailing legal-form tokens and a leading
    ``the``, then squeeze out spaces. Empty string means "no signal".
    """
    s = clean_display(name).lower().replace(".", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t]
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    return "".join(tokens)


def _slugify_tag(tag: str) -> str:
    """Canonical tag slug — mirrors tags._slug without raising on odd input."""
    return re.sub(r"\s+", "-", tag.strip().lower())


# ---- shared loaders -----------------------------------------------------------


async def _canonical_orgs(ctx: MCPContext, limit: int = 5000) -> list[Organization]:
    rows = (
        (
            await ctx.db.execute(
                select(Organization)
                .where(
                    Organization.canonical_owner_tenant_id == ctx.tenant_id,
                    Organization.merge_status == MergeStatus.canonical,
                )
                .order_by(Organization.display_name)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _org_edge_counts(ctx: MCPContext) -> dict[uuid.UUID, int]:
    """person_org_role edge count per org — used to pick the merge survivor."""
    rows = await ctx.db.execute(
        select(PersonOrgRole.organization_id, func.count())
        .group_by(PersonOrgRole.organization_id)
    )
    return {row[0]: int(row[1]) for row in rows if row[0] is not None}


# Role / shared inboxes legitimately attach to many distinct people, so a shared
# one is NOT a duplicate signal — skipped when grouping people by email.
_ROLE_LOCALPARTS: frozenset[str] = frozenset(
    {
        "info", "sales", "support", "admin", "contact", "hello", "office",
        "team", "no-reply", "noreply", "help", "billing", "accounts",
        "accounting", "hr", "careers", "jobs", "webmaster", "postmaster",
        "mail", "email", "service", "marketing", "press", "enquiries",
    }
)


def _suggest_person_survivor(members: list[dict[str, Any]]) -> uuid.UUID:
    """Best survivor: a full given+family name, then the longest display name."""

    def score(m: dict[str, Any]) -> tuple[int, int]:
        has_full = 1 if (m.get("given_name") and m.get("family_name")) else 0
        return (has_full, len(m.get("name") or ""))

    return max(members, key=score)["person_id"]


async def _person_email_rows(ctx: MCPContext) -> list[dict[str, Any]]:
    """Canonical persons joined to their (lowercased) emails for dup grouping.

    RLS + the explicit ``canonical_owner_tenant_id`` filter keep this to the
    caller's tenant; the join to ``persons`` scopes the otherwise tenant-less
    ``emails`` rows. Read-only — no merge is performed here.
    """
    rows = await ctx.db.execute(
        select(
            Person.id,
            Person.display_name,
            Person.given_name,
            Person.family_name,
            func.lower(Email.address).label("email"),
        )
        .join(Email, Email.person_id == Person.id)
        .where(
            Person.canonical_owner_tenant_id == ctx.tenant_id,
            Person.merge_status == MergeStatus.canonical,
        )
    )
    return [dict(r._mapping) for r in rows]


# ---- data_quality_report ------------------------------------------------------


class DataQualityReportInput(BaseModel):
    include_samples: bool = True
    sample_limit: int = Field(default=200, ge=1, le=2000)


class DataQualityReportOutput(ToolOutput):
    org_total: int
    issues: dict[str, list[dict[str, Any]]]
    org_duplicate_groups: list[dict[str, Any]]
    tag_variant_groups: list[dict[str, Any]]
    person_duplicate_groups: list[dict[str, Any]] = []
    summary: dict[str, int]


def _suggest_survivor(members: list[dict[str, Any]]) -> uuid.UUID:
    """Best survivor: most edges, then an already-clean name, then longest name."""
    def score(m: dict[str, Any]) -> tuple[int, int, int]:
        already_clean = 1 if m["name"] == clean_display(m["name"]) else 0
        return (m.get("edge_count", 0), already_clean, len(m["name"]))

    return max(members, key=score)["org_id"]


async def data_quality_report(
    ctx: MCPContext, req: DataQualityReportInput
) -> DataQualityReportOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ("org:read", "tag:read", "person:read"))

    orgs = await _canonical_orgs(ctx)
    edge_counts = await _org_edge_counts(ctx)

    quote_wrapped: list[dict[str, Any]] = []
    phone_as_name: list[dict[str, Any]] = []
    job_title_as_name: list[dict[str, Any]] = []
    whitespace: list[dict[str, Any]] = []
    by_key: dict[str, list[dict[str, Any]]] = {}

    for org in orgs:
        name = org.display_name or org.legal_name or ""
        cleaned = clean_display(name)
        entry = {"org_id": org.id, "name": name, "cleaned": cleaned, "edge_count": edge_counts.get(org.id, 0)}
        if is_phone_like(name):
            phone_as_name.append({"org_id": org.id, "name": name})
        elif strip_wrapping_quotes(name) != name.strip():
            quote_wrapped.append(entry)
        elif cleaned != name:
            whitespace.append(entry)
        # Independent signal (a job-title name can also be unique), so check it
        # outside the elif chain — surfaced read-only for human review on import.
        if looks_like_job_title(name):
            job_title_as_name.append({"org_id": org.id, "name": name})
        key = dedup_key(name)
        if key:
            by_key.setdefault(key, []).append(entry)

    duplicate_groups: list[dict[str, Any]] = []
    for key, members in by_key.items():
        if len(members) < 2:
            continue
        duplicate_groups.append(
            {
                "key": key,
                "members": members,
                "suggested_survivor_id": _suggest_survivor(members),
            }
        )
    duplicate_groups.sort(key=lambda g: len(g["members"]), reverse=True)

    # ---- tags ----
    tag_rows = (
        (
            await ctx.db.execute(
                select(PersonTenantMembership.tags).where(
                    PersonTenantMembership.tenant_id == ctx.tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    raw_counts: dict[str, int] = {}
    for tags in tag_rows:
        for t in tags or []:
            raw_counts[t] = raw_counts.get(t, 0) + 1
    by_slug: dict[str, dict[str, int]] = {}
    for raw, count in raw_counts.items():
        by_slug.setdefault(_slugify_tag(raw), {})[raw] = count
    tag_variant_groups: list[dict[str, Any]] = []
    for canonical, variants in by_slug.items():
        if len(variants) < 2:
            continue
        tag_variant_groups.append(
            {
                "canonical": canonical,
                "variants": sorted(
                    ({"tag": raw, "person_count": c} for raw, c in variants.items()),
                    key=lambda v: v["person_count"],
                    reverse=True,
                ),
            }
        )
    tag_variant_groups.sort(key=lambda g: len(g["variants"]), reverse=True)

    # ---- people (duplicate candidates grouped by a shared email) ----
    # Email is the highest-precision dedup signal (two canonical people sharing
    # a personal address are almost certainly the same person). Detection only —
    # the merge action is deliberately NOT wired here (see notes).
    person_rows = await _person_email_rows(ctx)
    by_email: dict[str, dict[uuid.UUID, dict[str, Any]]] = {}
    for r in person_rows:
        email = (r["email"] or "").strip().lower()
        if not email or "@" not in email:
            continue
        if email.split("@", 1)[0] in _ROLE_LOCALPARTS:
            continue
        by_email.setdefault(email, {})[r["id"]] = {
            "person_id": r["id"],
            "name": r["display_name"],
            "given_name": r["given_name"],
            "family_name": r["family_name"],
            "email": email,
        }
    person_duplicate_groups: list[dict[str, Any]] = []
    for email, members_map in by_email.items():
        if len(members_map) < 2:
            continue
        members = list(members_map.values())
        person_duplicate_groups.append(
            {
                "key": email,
                "shared_email": email,
                "members": members,
                "suggested_survivor_id": _suggest_person_survivor(members),
            }
        )
    person_duplicate_groups.sort(key=lambda g: len(g["members"]), reverse=True)

    lim = req.sample_limit if req.include_samples else 0
    issues = {
        "quote_wrapped": quote_wrapped[:lim],
        "phone_as_name": phone_as_name[:lim],
        "job_title_as_name": job_title_as_name[:lim],
        "whitespace": whitespace[:lim],
    }
    summary = {
        "quote_wrapped": len(quote_wrapped),
        "phone_as_name": len(phone_as_name),
        "job_title_as_name": len(job_title_as_name),
        "whitespace": len(whitespace),
        "duplicate_groups": len(duplicate_groups),
        "duplicate_orgs": sum(len(g["members"]) for g in duplicate_groups),
        "tag_variant_groups": len(tag_variant_groups),
        "person_duplicate_groups": len(person_duplicate_groups),
        "duplicate_people": sum(len(g["members"]) for g in person_duplicate_groups),
    }
    return DataQualityReportOutput(
        org_total=len(orgs),
        issues=issues,
        org_duplicate_groups=duplicate_groups[: req.sample_limit],
        tag_variant_groups=tag_variant_groups,
        person_duplicate_groups=person_duplicate_groups[: req.sample_limit],
        summary=summary,
    )


# ---- clean_org_names ----------------------------------------------------------


class CleanOrgNamesInput(BaseModel):
    org_ids: list[uuid.UUID] | None = None
    dry_run: bool = True
    confidence: float = Field(default=1.0, ge=0, le=1)


class CleanOrgNamesOutput(ToolOutput):
    dry_run: bool
    changed: list[dict[str, Any]]
    changed_count: int
    skipped_phone_like: int
    status: str


async def clean_org_names(ctx: MCPContext, req: CleanOrgNamesInput) -> CleanOrgNamesOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("org:write",))

    if req.org_ids is not None:
        wanted = set(req.org_ids)
        orgs = [o for o in await _canonical_orgs(ctx) if o.id in wanted]
    else:
        orgs = await _canonical_orgs(ctx)

    changed: list[dict[str, Any]] = []
    skipped_phone = 0
    for org in orgs:
        disp = org.display_name or ""
        legal = org.legal_name or ""
        new_disp = clean_display(disp)
        new_legal = clean_display(legal)
        if new_disp == disp and new_legal == legal:
            continue
        # A phone-only "name" has nothing to clean *to* — leave for merge/manual.
        if is_phone_like(disp) and is_phone_like(legal):
            skipped_phone += 1
            continue
        record = {
            "org_id": org.id,
            "display_before": disp,
            "display_after": new_disp,
            "legal_before": legal,
            "legal_after": new_legal,
        }
        changed.append(record)
        if not req.dry_run:
            before = {"display_name": disp, "legal_name": legal, "etag": org.etag}
            org.display_name = new_disp or disp
            org.legal_name = new_legal or legal
            org.etag = uuid.uuid4().hex
            await ctx.db.flush()
            await emit_action_event(
                ctx,
                event_type="organization.normalize_name",
                aggregate_type="organization",
                aggregate_id=org.id,
                payload_before=before,
                payload_after={
                    "display_name": org.display_name,
                    "legal_name": org.legal_name,
                    "etag": org.etag,
                },
                confidence=req.confidence,
            )

    return CleanOrgNamesOutput(
        dry_run=req.dry_run,
        changed=changed,
        changed_count=len(changed),
        skipped_phone_like=skipped_phone,
        status="planned" if req.dry_run else "applied",
    )


# ---- canonicalize_tags --------------------------------------------------------


class CanonicalizeTagsInput(BaseModel):
    dry_run: bool = True
    confidence: float = Field(default=1.0, ge=0, le=1)


class CanonicalizeTagsOutput(ToolOutput):
    dry_run: bool
    mapping: dict[str, str]
    memberships_changed: int
    sample: list[dict[str, Any]]
    status: str


async def canonicalize_tags(
    ctx: MCPContext, req: CanonicalizeTagsInput
) -> CanonicalizeTagsOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("tag:write",))

    memberships = (
        (
            await ctx.db.execute(
                select(PersonTenantMembership).where(
                    PersonTenantMembership.tenant_id == ctx.tenant_id
                )
            )
        )
        .scalars()
        .all()
    )

    # Build the variant -> canonical mapping (only where a variant != its slug).
    mapping: dict[str, str] = {}
    for m in memberships:
        for t in m.tags or []:
            slug = _slugify_tag(t)
            if slug and slug != t:
                mapping[t] = slug

    sample: list[dict[str, Any]] = []
    changed = 0
    for m in memberships:
        old = list(m.tags or [])
        if not old:
            continue
        new = sorted({_slugify_tag(t) for t in old if t.strip()})
        if new == sorted(old):
            continue
        changed += 1
        if len(sample) < 20:
            sample.append({"person_id": m.person_id, "before": old, "after": new})
        if not req.dry_run:
            before = {"tags": old}
            m.tags = new
            await ctx.db.flush()
            await emit_action_event(
                ctx,
                event_type="tag.canonicalize",
                aggregate_type="tag",
                aggregate_id=m.person_id,
                affected_ids=[m.person_id],
                payload_before=before,
                payload_after={"tags": new},
                confidence=req.confidence,
            )

    return CanonicalizeTagsOutput(
        dry_run=req.dry_run,
        mapping=mapping,
        memberships_changed=changed,
        sample=sample,
        status="planned" if req.dry_run else "applied",
    )


# ---- merge_organizations / unmerge_organizations ------------------------------


async def _load_org(ctx: MCPContext, org_id: uuid.UUID) -> Organization:
    org = await ctx.db.get(Organization, org_id)
    if org is None or org.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(ORG_NOT_FOUND, f"no organization with id {org_id}", retryable=False)
    return org


async def _enqueue_org_graph_sync(
    ctx: MCPContext, survivor_id: uuid.UUID, loser_id: uuid.UUID, op: str
) -> None:
    """Best-effort FalkorDB sync. Never fails the merge if the outbox is absent."""
    import json

    try:
        await ctx.db.execute(
            text(
                "INSERT INTO event_outbox (channel, tenant_id, payload) "
                "VALUES (:channel, CAST(:tenant_id AS uuid), CAST(:payload AS jsonb))"
            ),
            {
                "channel": "falkordb.edge.create",
                "tenant_id": str(ctx.tenant_id),
                "payload": json.dumps(
                    {
                        "type": op,
                        "entity": "organization",
                        "survivor_id": str(survivor_id),
                        "alias_id": str(loser_id),
                        "edge_label": "dedup:alias_of",
                    }
                ),
            },
        )
    except Exception:  # noqa: BLE001 — graph sync is advisory, not load-bearing
        pass


class MergeOrganizationsInput(BaseModel):
    survivor_id: uuid.UUID
    loser_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    dry_run: bool = True
    confidence: float = Field(default=1.0, ge=0, le=1)


class MergeOrganizationsOutput(ToolOutput):
    dry_run: bool
    survivor_id: str
    merged: list[str]
    roles_repointed: int
    merge_event_id: str | None = None
    status: str


async def merge_organizations(
    ctx: MCPContext, req: MergeOrganizationsInput
) -> MergeOrganizationsOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("org:write",))

    losers = [lid for lid in req.loser_ids if lid != req.survivor_id]
    if not losers:
        raise ToolError(VALIDATION_FAILED, "loser_ids must differ from survivor_id", retryable=False)

    survivor = await _load_org(ctx, req.survivor_id)
    if survivor.merge_status != MergeStatus.canonical:
        raise ToolError(VALIDATION_FAILED, "survivor is not a canonical organization", retryable=False)

    # Resolve each loser's roles up front (needed to make the merge reversible).
    repointed: dict[str, list[str]] = {}
    total_roles = 0
    loser_orgs: list[Organization] = []
    for lid in losers:
        loser = await _load_org(ctx, lid)
        if loser.merge_status != MergeStatus.canonical:
            raise ToolError(
                VALIDATION_FAILED, f"organization {lid} is already merged/archived", retryable=False
            )
        loser_orgs.append(loser)
        role_ids = (
            (await ctx.db.execute(select(PersonOrgRole.id).where(PersonOrgRole.organization_id == lid)))
            .scalars()
            .all()
        )
        repointed[str(lid)] = [str(r) for r in role_ids]
        total_roles += len(role_ids)

    if req.dry_run:
        return MergeOrganizationsOutput(
            dry_run=True,
            survivor_id=str(req.survivor_id),
            merged=[str(lid) for lid in losers],
            roles_repointed=total_roles,
            status="planned",
        )

    for loser in loser_orgs:
        # Re-point this loser's employment edges onto the survivor. Per-person
        # primary uniqueness can't be violated: a valid pre-merge state already
        # holds <=1 active primary role per person across all orgs.
        await ctx.db.execute(
            update(PersonOrgRole)
            .where(PersonOrgRole.organization_id == loser.id)
            .values(organization_id=req.survivor_id)
        )
        loser.merge_status = MergeStatus.merged_into
        loser.merged_into_id = req.survivor_id
        loser.etag = uuid.uuid4().hex
        await ctx.db.flush()
        await _enqueue_org_graph_sync(ctx, req.survivor_id, loser.id, "org_merge_applied")

    event_id = await emit_action_event(
        ctx,
        event_type="organization.merge_applied",
        aggregate_type="organization",
        aggregate_id=req.survivor_id,
        affected_ids=losers,
        payload_before=None,
        payload_after={
            "survivor_id": str(req.survivor_id),
            "loser_ids": [str(lid) for lid in losers],
            "roles_repointed": total_roles,
        },
        evidence={"survivor_id": str(req.survivor_id), "repointed": repointed},
        confidence=req.confidence,
    )

    return MergeOrganizationsOutput(
        dry_run=False,
        survivor_id=str(req.survivor_id),
        merged=[str(lid) for lid in losers],
        roles_repointed=total_roles,
        merge_event_id=str(event_id),
        status="applied",
    )


class UnmergeOrganizationsInput(BaseModel):
    merge_event_id: uuid.UUID


class UnmergeOrganizationsOutput(ToolOutput):
    survivor_id: str
    restored: list[str]
    roles_restored: int
    status: str


async def unmerge_organizations(
    ctx: MCPContext, req: UnmergeOrganizationsInput
) -> UnmergeOrganizationsOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ("org:write",))

    ev = (
        await ctx.audit_db.execute(
            text(
                "SELECT event_type, tenant_id, evidence FROM action_event "
                "WHERE event_id = CAST(:eid AS uuid)"
            ),
            {"eid": str(req.merge_event_id)},
        )
    ).mappings().first()
    if ev is None or ev["event_type"] != "organization.merge_applied":
        raise ToolError(VALIDATION_FAILED, "no organization.merge_applied event with that id", retryable=False)
    if uuid.UUID(str(ev["tenant_id"])) != ctx.tenant_id:
        raise ToolError(VALIDATION_FAILED, "tenant mismatch", retryable=False)

    evidence = ev["evidence"] or {}
    survivor_id = uuid.UUID(str(evidence["survivor_id"]))
    repointed: dict[str, list[str]] = evidence.get("repointed", {})

    restored_roles = 0
    restored_orgs: list[str] = []
    for loser_str, role_ids in repointed.items():
        loser_id = uuid.UUID(loser_str)
        if role_ids:
            await ctx.db.execute(
                update(PersonOrgRole)
                .where(PersonOrgRole.id.in_([uuid.UUID(r) for r in role_ids]))
                .values(organization_id=loser_id)
            )
            restored_roles += len(role_ids)
        loser = await ctx.db.get(Organization, loser_id)
        if loser is not None:
            loser.merge_status = MergeStatus.canonical
            loser.merged_into_id = None
            loser.etag = uuid.uuid4().hex
        restored_orgs.append(loser_str)
        await _enqueue_org_graph_sync(ctx, survivor_id, loser_id, "org_unmerge")
    await ctx.db.flush()

    await emit_action_event(
        ctx,
        event_type="organization.unmerge",
        aggregate_type="organization",
        aggregate_id=survivor_id,
        affected_ids=[uuid.UUID(s) for s in restored_orgs],
        payload_before=None,
        payload_after={"survivor_id": str(survivor_id), "restored": restored_orgs},
        evidence={"supersedes_event_id": str(req.merge_event_id)},
    )

    return UnmergeOrganizationsOutput(
        survivor_id=str(survivor_id),
        restored=restored_orgs,
        roles_restored=restored_roles,
        status="applied",
    )


# ---- registration -------------------------------------------------------------


def register_data_quality_tools() -> None:
    register(
        name="data_quality_report",
        description=(
            "Read-only scan of organizations, tags, and people for dirty data: "
            "literal quote-wrapped names, phone-as-name, whitespace, duplicate "
            "org/person candidate groups, and case/format tag variants. People are "
            "grouped by a shared email. Requires CLIENT + org:read,tag:read,person:read."
        ),
        input_model=DataQualityReportInput,
        output_model=DataQualityReportOutput,
        handler=data_quality_report,
        required_role="CLIENT",
        required_scopes=("org:read", "tag:read", "person:read"),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="clean_org_names",
        description=(
            "Strip literal wrapping quotes and collapse whitespace on organization "
            "names. dry_run defaults True (returns the plan); set dry_run=false to "
            "apply (audited + reversible). Requires STAFF + org:write."
        ),
        input_model=CleanOrgNamesInput,
        output_model=CleanOrgNamesOutput,
        handler=clean_org_names,
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
    register(
        name="canonicalize_tags",
        description=(
            "Casefold/slugify tag variants so 'Folly Beach' and 'folly-beach' "
            "collapse to one tag across all people. dry_run defaults True. "
            "Requires STAFF + tag:write."
        ),
        input_model=CanonicalizeTagsInput,
        output_model=CanonicalizeTagsOutput,
        handler=canonicalize_tags,
        required_role="STAFF",
        required_scopes=("tag:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="merge_organizations",
        description=(
            "Merge duplicate organizations into a survivor: re-point employment "
            "edges, mark losers merged. dry_run defaults True; fully reversible "
            "via unmerge_organizations. Requires STAFF + org:write."
        ),
        input_model=MergeOrganizationsInput,
        output_model=MergeOrganizationsOutput,
        handler=merge_organizations,
        required_role="STAFF",
        required_scopes=("org:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="none",
    )
    register(
        name="unmerge_organizations",
        description=(
            "Reverse a previous organization merge by its merge_event_id — "
            "restores the losers to canonical and re-points their edges back. "
            "Requires STAFF + org:write."
        ),
        input_model=UnmergeOrganizationsInput,
        output_model=UnmergeOrganizationsOutput,
        handler=unmerge_organizations,
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


register_data_quality_tools()
