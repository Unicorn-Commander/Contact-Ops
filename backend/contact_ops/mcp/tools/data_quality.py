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
from sqlalchemy import func, select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register
from contact_ops.models import Organization, PersonOrgRole, PersonTenantMembership
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


# ---- data_quality_report ------------------------------------------------------


class DataQualityReportInput(BaseModel):
    include_samples: bool = True
    sample_limit: int = Field(default=200, ge=1, le=2000)


class DataQualityReportOutput(ToolOutput):
    org_total: int
    issues: dict[str, list[dict[str, Any]]]
    org_duplicate_groups: list[dict[str, Any]]
    tag_variant_groups: list[dict[str, Any]]
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
    require_scopes(ctx, ("org:read", "tag:read"))

    orgs = await _canonical_orgs(ctx)
    edge_counts = await _org_edge_counts(ctx)

    quote_wrapped: list[dict[str, Any]] = []
    phone_as_name: list[dict[str, Any]] = []
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

    lim = req.sample_limit if req.include_samples else 0
    issues = {
        "quote_wrapped": quote_wrapped[:lim],
        "phone_as_name": phone_as_name[:lim],
        "whitespace": whitespace[:lim],
    }
    summary = {
        "quote_wrapped": len(quote_wrapped),
        "phone_as_name": len(phone_as_name),
        "whitespace": len(whitespace),
        "duplicate_groups": len(duplicate_groups),
        "duplicate_orgs": sum(len(g["members"]) for g in duplicate_groups),
        "tag_variant_groups": len(tag_variant_groups),
    }
    return DataQualityReportOutput(
        org_total=len(orgs),
        issues=issues,
        org_duplicate_groups=duplicate_groups[: req.sample_limit],
        tag_variant_groups=tag_variant_groups,
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


# ---- registration -------------------------------------------------------------


def register_data_quality_tools() -> None:
    register(
        name="data_quality_report",
        description=(
            "Read-only scan of organizations and tags for dirty data: literal "
            "quote-wrapped names, phone-as-name, whitespace, duplicate-candidate "
            "groups, and case/format tag variants. Requires CLIENT + org:read,tag:read."
        ),
        input_model=DataQualityReportInput,
        output_model=DataQualityReportOutput,
        handler=data_quality_report,
        required_role="CLIENT",
        required_scopes=("org:read", "tag:read"),
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


register_data_quality_tools()
