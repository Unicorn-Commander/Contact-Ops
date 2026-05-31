/**
 * ReviewQueuePreview — DEV-only, auth-free preview of the Design Language v2
 * Review Queue.
 *
 * Route: /design-system/review-queue (registered in routes/index.tsx, DEV only).
 *
 * Mounts the REAL Review-Queue components — ClusterCard (which renders the REAL
 * ProposalRow), the REAL ConflictBanner, and the REAL QualityFilterChips +
 * BulkApproveBar + BulkActionBar — over a representative sample list so the
 * orchestrator can screenshot-gate the redesign WITHOUT a Keycloak session or
 * the MCP backend. The handlers are local no-ops (this is a preview surface,
 * not the live page); the *components* and their styling are the real ones.
 *
 * The sample is varied on purpose: a high-confidence merge, a good-confidence
 * enrichment, a review-tier voice match flagged cross-tenant, a low-confidence
 * relationship link flagged HIPAA, a multi-proposal cluster, and a conflict.
 *
 * Accessibility: text targets WCAG AA; the v2 row animations are gated behind
 * prefers-reduced-motion in tokens-v2.css (same as the live page).
 */
import { useEffect, useMemo, useState } from "react";
import { ListChecks, Moon, Sun } from "lucide-react";

import { ClusterCard } from "@/components/inbox/ClusterCard";
import { ConflictBanner } from "@/components/inbox/ConflictBanner";
import { EmptyStateInboxZero } from "@/components/inbox/EmptyStateInboxZero";
import {
  BulkApproveBar,
} from "@/components/inbox/BulkApproveBar";
import { BulkActionBar } from "@/components/inbox/BulkActionBar";
import {
  EMPTY_QUALITY_FILTER,
  QualityFilterChips,
  proposalMatchesQualityFilter,
  qualityFilterIsEmpty,
  type QualityFilterState,
} from "@/components/inbox/QualityFilterChips";
import { Button } from "@/components/ui/button";
import { MonoNumeric } from "@/design-system";
import {
  applyThemePreference,
  getThemePreference,
  type ThemeMode,
} from "@/design-system/tokens";
import type { Proposal, ProposalCluster } from "@/lib/types";

/* ------------------------------------------------ sample proposal factory */

let seq = 0;
function makeProposal(overrides: Partial<Proposal> & { agent_id: string; action_type: string }): Proposal {
  const id = `pp-${seq++}`;
  const base: Proposal = {
    proposal_id: id,
    action_event_id: `ae-${id}`,
    agent_id: overrides.agent_id,
    agent_version: "1.0",
    tenant_id: "tenant-1",
    tenant_slug: "demo",
    entity_id: `e-${id}`,
    entity_kind: "person",
    entity_display_name: "Unknown",
    action_type: overrides.action_type,
    payload_before: null,
    payload_after: {},
    confidence: 0.8,
    reversibility_class: "reversible",
    compliance: { hipaa: false, exposure: "none" },
    trust_tier_at_creation: 1,
    trace_id: null,
    evidence_pack_id: null,
    parent_proposal_id: null,
    rationale: "",
    created_at: new Date().toISOString(),
    snoozed_until: null,
    cross_tenant: false,
    touches_case_file: false,
    case_file_links: [],
    fields_changed: 1,
    is_dedup: false,
    is_edge: false,
    is_delete: false,
    bulk_count: 1,
    cluster_id: "c-0",
  };
  return { ...base, ...overrides };
}

const now = Date.now();
const ago = (ms: number) => new Date(now - ms).toISOString();

// 1 — high-confidence merge (dedup)
const pMerge = makeProposal({
  agent_id: "dedupe-agent",
  agent_version: "2.3",
  action_type: "dedup.merge",
  entity_display_name: "Mina Chen",
  confidence: 0.96,
  is_dedup: true,
  rationale: "Email, phone, and employer overlap with the canonical contact.",
  created_at: ago(12_000),
  cluster_id: "c-1",
});

// 2 — good-confidence enrichment (3 fields)
const pEnrich = makeProposal({
  agent_id: "enrichment-agent",
  agent_version: "1.8",
  action_type: "enrichment.fields",
  entity_display_name: "Rafael Ortiz",
  confidence: 0.82,
  payload_after: { title: "VP Design", company: "Northwind Labs", location: "Austin, TX" },
  fields_changed: 3,
  rationale: "Data Intel and CardDAV agree on title, company, and location.",
  created_at: ago(4 * 60_000),
  cluster_id: "c-2",
});

// 3 — review-tier voice match, flagged cross-tenant
const pVoice = makeProposal({
  agent_id: "voice-match-agent",
  agent_version: "0.9",
  action_type: "voice_match.attach",
  entity_display_name: "Devon Walsh",
  confidence: 0.61,
  cross_tenant: true,
  rationale: "Meeting-Ops voice print matched two recent calls.",
  created_at: ago(9 * 60_000),
  cluster_id: "c-3",
});

// 4 — low-confidence relationship link, flagged HIPAA
const pRel = makeProposal({
  agent_id: "relationship-agent",
  agent_version: "1.1",
  action_type: "relationship.link",
  entity_display_name: "Maya Hart",
  confidence: 0.44,
  compliance: { hipaa: true, exposure: "high" },
  rationale: "Co-occurrence in 3 source events suggests a works-with edge.",
  created_at: ago(21 * 60_000),
  cluster_id: "c-4",
});

// 5 — a multi-proposal cluster (two enrichments + a tag) on one entity
const pClusterA = makeProposal({
  agent_id: "enrichment-agent",
  agent_version: "1.8",
  action_type: "enrichment.fields",
  entity_display_name: "Avery Hart",
  confidence: 0.91,
  payload_after: { title: "Founder" },
  created_at: ago(2 * 60_000),
  cluster_id: "c-5",
});
const pClusterB = makeProposal({
  agent_id: "tag-agent",
  agent_version: "1.2",
  action_type: "tag.add",
  entity_display_name: "Avery Hart",
  confidence: 0.78,
  payload_after: { tag: "investor" },
  created_at: ago(3 * 60_000),
  cluster_id: "c-5",
});
const pClusterC = makeProposal({
  agent_id: "carddav-reconcile",
  agent_version: "0.7",
  action_type: "carddav.reconcile",
  entity_display_name: "Avery Hart",
  confidence: 0.55,
  created_at: ago(6 * 60_000),
  cluster_id: "c-5",
});

// 6 — two agents that conflict on the same field (for ConflictBanner)
const pConflictA = makeProposal({
  agent_id: "enrichment-agent",
  agent_version: "1.8",
  action_type: "enrichment.fields",
  entity_display_name: "John Rector",
  confidence: 0.74,
  payload_after: { title: "CEO" },
  rationale: "Company About page lists him as CEO.",
  created_at: ago(30 * 60_000),
  cluster_id: "c-6",
});
const pConflictB = makeProposal({
  agent_id: "data-intel-agent",
  agent_version: "2.0",
  action_type: "enrichment.fields",
  entity_display_name: "John Rector",
  confidence: 0.69,
  payload_after: { title: "Former CEO" },
  rationale: "SEC EDGAR filing 2026-03-12 lists him as former CEO.",
  created_at: ago(31 * 60_000),
  cluster_id: "c-6",
});

function cluster(
  cluster_id: string,
  proposals: Proposal[],
  extra?: Partial<ProposalCluster>,
): { cluster: ProposalCluster; proposals: Proposal[] } {
  const first = proposals[0];
  const avg = proposals.reduce((s, p) => s + p.confidence, 0) / proposals.length;
  const meta: ProposalCluster = {
    cluster_id,
    cluster_kind: "entity",
    entity_id: first.entity_id,
    entity_display_name: first.entity_display_name,
    entity_avatar_url: null,
    tenant_id: first.tenant_id,
    proposal_ids: proposals.map((p) => p.proposal_id),
    cumulative_confidence_avg: avg,
    agent_slugs: Array.from(new Set(proposals.map((p) => p.agent_id))),
    earliest_created_at: proposals.at(-1)?.created_at ?? first.created_at,
    latest_created_at: first.created_at,
    ...extra,
  };
  return { cluster: meta, proposals };
}

// The conflict cluster carries a `conflict` flag (ClusterCard surfaces conflicts
// via the `hasConflict` prop, not via cluster_kind).
const SAMPLE_CLUSTERS: {
  cluster: ProposalCluster;
  proposals: Proposal[];
  conflict?: boolean;
}[] = [
  cluster("c-1", [pMerge]),
  cluster("c-2", [pEnrich]),
  cluster("c-3", [pVoice]),
  cluster("c-4", [pRel]),
  cluster("c-5", [pClusterA, pClusterB, pClusterC]),
  { ...cluster("c-6", [pConflictA, pConflictB]), conflict: true },
];

const ALL_SAMPLE_PROPOSALS = SAMPLE_CLUSTERS.flatMap((c) => c.proposals);

/* ----------------------------------------------------------------- page */

export function ReviewQueuePreview() {
  const [theme, setTheme] = useState<ThemeMode>(() => {
    const requested = new URLSearchParams(window.location.search).get("theme");
    return requested === "light" || requested === "dark" ? requested : getThemePreference();
  });
  useEffect(() => {
    applyThemePreference(theme);
  }, [theme]);

  const reduced = useMemo(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  // Start with the multi-proposal + conflict clusters expanded so the
  // screenshot shows real rows without interaction.
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(["c-1", "c-2", "c-3", "c-4", "c-5"]),
  );
  const [focusedId, setFocusedId] = useState<string | null>(pMerge.proposal_id);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    () => new Set([pEnrich.proposal_id, pClusterA.proposal_id]),
  );

  const [filter, setFilter] = useState<QualityFilterState>(EMPTY_QUALITY_FILTER);
  const matched = useMemo(
    () =>
      qualityFilterIsEmpty(filter)
        ? ALL_SAMPLE_PROPOSALS
        : ALL_SAMPLE_PROPOSALS.filter((p) => proposalMatchesQualityFilter(p, filter)),
    [filter],
  );

  const nextTheme = theme === "dark" ? "light" : "dark";

  function toggleCluster(id: string) {
    setExpanded((prev) => {
      const out = new Set(prev);
      if (out.has(id)) out.delete(id);
      else out.add(id);
      return out;
    });
  }

  return (
    <main className="min-h-screen bg-background p-4 text-foreground lg:p-8">
      <div className="mx-auto max-w-[1100px] space-y-8">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-5">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-[var(--radius-md)] border border-[oklch(var(--co-brand-300)/0.3)] bg-[oklch(var(--co-brand-500)/0.12)] text-[oklch(var(--co-brand-300))]">
              <ListChecks className="h-5 w-5" strokeWidth={1.8} />
            </span>
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                Contact-Ops · Design Language v2
              </p>
              <h1 className="text-xl font-semibold tracking-tight">Review Queue — calm row preview</h1>
              <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
                DEV-only, auth-free. Mounts the real ClusterCard / ProposalRow / ConflictBanner /
                bulk surfaces over a representative sample (high + low confidence, cross-tenant,
                HIPAA, a cluster, a conflict).
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {reduced ? (
              <span className="rounded-full border border-border bg-muted px-2.5 py-1 text-[11px] text-muted-foreground">
                reduced-motion: static
              </span>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setTheme(nextTheme)}
              aria-label={`Switch to ${nextTheme} mode`}
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              <span className="capitalize">{nextTheme}</span>
            </Button>
          </div>
        </header>

        {/* Toolbar surfaces: quality filter chips + filter-driven bulk bar */}
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">Quality filter + filter-driven bulk approve</h2>
          <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-card">
            <QualityFilterChips
              value={filter}
              matchedCount={matched.length}
              totalCount={ALL_SAMPLE_PROPOSALS.length}
              onChange={setFilter}
            />
            <BulkApproveBar
              visible={!qualityFilterIsEmpty(filter)}
              matchedCount={matched.length}
              busy={false}
              progress={null}
              onApproveAll={() => undefined}
              onRejectAll={() => undefined}
            />
          </div>
        </section>

        {/* The clusters of calm proposal rows (the core redesign) */}
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">Proposal clusters (calm rows)</h2>
          <div className="space-y-co-8">
            {SAMPLE_CLUSTERS.map(({ cluster: c, proposals, conflict }) => (
              <ClusterCard
                key={c.cluster_id}
                cluster={c}
                proposals={proposals}
                expanded={expanded.has(c.cluster_id)}
                onToggle={() => toggleCluster(c.cluster_id)}
                focusedProposalId={focusedId}
                selectedProposalIds={selectedIds}
                onSelectProposal={(p) => setFocusedId(p.proposal_id)}
                onFocusProposal={(p) => setFocusedId(p.proposal_id)}
                onQuickApprove={() => undefined}
                onQuickReject={() => undefined}
                onApproveAll={() => undefined}
                onRejectAll={() => undefined}
                onOpenDetail={() => undefined}
                hasConflict={conflict ?? false}
              />
            ))}
          </div>
        </section>

        {/* Conflict banner (never auto-resolve; equal real estate; keep-both first-class) */}
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">Conflict banner</h2>
          <ConflictBanner
            fieldName="title"
            a={{ proposal: pConflictA, proposedValue: "CEO", source: "Company About page" }}
            b={{ proposal: pConflictB, proposedValue: "Former CEO", source: "SEC EDGAR, 2026-03-12" }}
            onKeepA={() => undefined}
            onKeepB={() => undefined}
            onKeepBoth={() => undefined}
            onCustomValue={() => undefined}
            onEscalate={() => undefined}
          />
        </section>

        {/* Inbox-zero empty state */}
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">Inbox-zero empty state</h2>
          <div className="rounded-[var(--radius-lg)] border border-border bg-card/40 p-4">
            <EmptyStateInboxZero
              stats={{
                today: 0,
                approved: 18,
                rejected: 4,
                snoozed: 2,
                topAgentToday: "enrichment-agent",
                topAgentCount: 11,
                bestCalibratedAgent: "dedupe-agent",
                bestCalibratedRate: 0.97,
                nextBatchEtaMinutes: 26,
              }}
              onReviewSnoozed={() => undefined}
              onOpenAgentReport={() => undefined}
              onOpenAutoApproveSettings={() => undefined}
            />
          </div>
        </section>

        <p className="text-center text-xs text-muted-foreground">
          Selection-driven bulk bar (slides up at ≥2 selected) ·{" "}
          <MonoNumeric tone="muted">{selectedIds.size}</MonoNumeric> selected in this preview
        </p>
      </div>

      {/* Selection-driven bulk action bar — fixed at the bottom, shows when ≥2
          selected (two rows are pre-selected above). */}
      <BulkActionBar
        selectionCount={selectedIds.size}
        eligibleCount={Math.max(0, selectedIds.size - 1)}
        blockedHipaa={0}
        blockedTier4={0}
        blockedCrossTenant={0}
        tenantSlugs={["demo"]}
        onApproveAll={() => undefined}
        onRejectAll={() => undefined}
        onSnoozeAll={() => undefined}
        onClear={() => setSelectedIds(new Set())}
      />
    </main>
  );
}

export default ReviewQueuePreview;
