/**
 * /inbox - the Phase 3.3b approval inbox cockpit.
 *
 * Layout (desktop >=1024px):
 *   Header: stats, filter chips, FocusMode
 *   Nav / virtualized cluster list / detail pane
 *   BulkActionBar slides up when N >= 2 selected.
 *
 * Mobile (<1024px): single-pane list; swipe-right approves T1, swipe-
 * left snoozes; long-press opens detail; T4/dedup/bulk blocked with
 * "send link to desktop" toast.
 *
 * Keyboard (active when /inbox is the route): J/K nav · E expand
 * detail · Y approve · N reject · H snooze · M mute · D dismiss · Esc
 * clear, Cmd+A select all, Cmd+K command palette, Cmd+. focus mode, Cmd+Z undo.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { HotkeysProvider } from "react-hotkeys-hook";
import { useNavigate } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { AlertTriangle, Bell, ExternalLink, Monitor } from "lucide-react";
import { toast } from "sonner";

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { ClusterCard } from "@/components/inbox/ClusterCard";
import { FocusModeToggle } from "@/components/inbox/FocusModeToggle";
import { BulkActionBar } from "@/components/inbox/BulkActionBar";
import { CommandPalette } from "@/components/inbox/CommandPalette";
import { EmptyStateInboxZero } from "@/components/inbox/EmptyStateInboxZero";
import { EvidencePanel } from "@/components/inbox/EvidencePanel";
import { StructuredDiff, entriesFromPayload } from "@/components/inbox/StructuredDiff";
import { TieredApproveButton } from "@/components/inbox/TieredApproveButton";
import { SnoozePicker } from "@/components/inbox/SnoozePicker";
import {
  ActionAttribution,
  AgentBadge,
  ConfidenceIndicator,
  DensityControl,
  DensityProvider,
  KeyboardHint,
  MonoNumeric,
  Spinner,
  defaultDensityForSurface,
} from "@/design-system";

import {
  useApproveMutation,
  useBulkApproveMutation,
  useBulkRejectMutation,
  useInboxList,
  useRejectMutation,
  useSnoozeMutation,
  type InboxFilters,
} from "@/hooks/useInbox";
import { TypedPhraseConfirm } from "@/components/inbox/TypedPhraseConfirm";
import { useFocusMode } from "@/hooks/useFocusMode";
import { applyFocusMode, hydrateClusters } from "@/lib/inbox/clusterGrouping";
import { selectTier } from "@/lib/inbox/tierSelector";
import {
  INBOX_HOTKEY_SCOPE,
  INBOX_SHORTCUT_DESCRIPTIONS,
  useInboxShortcuts,
} from "@/lib/inbox/keyboardShortcuts";
import type { FieldChoice, StructuredFieldEntry } from "@/components/inbox/StructuredDiff";
import type { Proposal } from "@/lib/types";
import { cn } from "@/lib/utils";

type View = "needs-review" | "snoozed" | "resolved";

const PANE_DEFAULTS = { nav: "18%", list: "42%", detail: "40%" };

export function InboxRoute() {
  return (
    <HotkeysProvider initiallyActiveScopes={[INBOX_HOTKEY_SCOPE]}>
      <DensityProvider defaultDensity={defaultDensityForSurface("inbox")}>
        <InboxCockpit />
      </DensityProvider>
    </HotkeysProvider>
  );
}

function InboxCockpit() {
  const navigate = useNavigate();

  const [view, setView] = useState<View>("needs-review");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [expandedClusters, setExpandedClusters] = useState<Set<string>>(new Set());
  const [fieldChoicesByProposal, setFieldChoicesByProposal] = useState<
    Record<string, Record<string, FieldChoice>>
  >({});
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);
  const [bulkConfirmCount, setBulkConfirmCount] = useState(0);
  const [conflictsOnly, setConflictsOnly] = useState(false);
  const [tenantIds, setTenantIds] = useState<string[] | undefined>(undefined);
  const [agentFilter, setAgentFilter] = useState<string | null>(null);
  const [hipaaOnlyFromUrl, setHipaaOnlyFromUrl] = useState(false);

  const focusMode = useFocusMode();

  function readUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const tenantParam = params.get("tenant");
    const agentParam = params.get("agent");
    const hipaaParam = params.get("hipaa_only");
    setTenantIds(tenantParam ? [tenantParam] : undefined);
    setAgentFilter(agentParam);
    setHipaaOnlyFromUrl(hipaaParam === "true");
  }

  // Read URL params on mount
  useEffect(() => {
    readUrlParams();
  }, []);

  // Keep URL params in sync
  useEffect(() => {
    const handler = readUrlParams;
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);

  const filters: InboxFilters = useMemo(
    () => ({
      status: view === "needs-review" ? "proposed" : view === "snoozed" ? "snoozed" : "resolved",
      conflictsOnly: conflictsOnly || undefined,
      tenantIds: tenantIds,
      agentSlugs: agentFilter ? [agentFilter] : undefined,
      hipaaOnly: hipaaOnlyFromUrl || undefined,
    }),
    [view, conflictsOnly, tenantIds, agentFilter, hipaaOnlyFromUrl],
  );
  const list = useInboxList(filters);
  const approve = useApproveMutation(filters);
  const reject = useRejectMutation(filters);
  const snooze = useSnoozeMutation(filters);
  const bulkApprove = useBulkApproveMutation();
  const bulkReject = useBulkRejectMutation();

  // Flatten pages
  const allProposalsRaw: Proposal[] = useMemo(
    () => list.data?.pages.flatMap((p) => p.proposals) ?? [],
    [list.data],
  );
  const allClustersRaw = useMemo(
    () => list.data?.pages.flatMap((p) => p.clusters) ?? [],
    [list.data],
  );

  const visibleProposals = useMemo(
    () => applyFocusMode(allProposalsRaw, focusMode.enabled),
    [allProposalsRaw, focusMode.enabled],
  );
  const hydrated = useMemo(
    () => hydrateClusters(allClustersRaw, visibleProposals),
    [allClustersRaw, visibleProposals],
  );

  // Default-expand the first 3 clusters when needs-review small
  useEffect(() => {
    if (view !== "needs-review") return;
    if (expandedClusters.size > 0) return;
    setExpandedClusters(new Set(hydrated.slice(0, 3).map((c) => c.cluster_id)));
  }, [view, hydrated, expandedClusters.size]);

  // Auto-focus first proposal when list arrives + nothing focused
  useEffect(() => {
    if (focusedId) return;
    const first = hydrated[0]?.proposals[0];
    if (first) setFocusedId(first.proposal_id);
  }, [hydrated, focusedId]);

  const focusedProposal = useMemo(
    () => visibleProposals.find((p) => p.proposal_id === focusedId) ?? null,
    [visibleProposals, focusedId],
  );
  const focusedTier = focusedProposal
    ? selectTier(focusedProposal, { tenantAutoApprove: {} })
    : 1;

  // ---- helpers ----
  function focusDelta(delta: 1 | -1) {
    if (visibleProposals.length === 0) return;
    const idx = focusedId ? visibleProposals.findIndex((p) => p.proposal_id === focusedId) : -1;
    const next = Math.max(0, Math.min(visibleProposals.length - 1, idx + delta));
    const target = visibleProposals[next];
    if (target) {
      setFocusedId(target.proposal_id);
      // Make sure its cluster is expanded
      setExpandedClusters((prev) => {
        if (prev.has(target.cluster_id)) return prev;
        const out = new Set(prev);
        out.add(target.cluster_id);
        return out;
      });
    }
  }

  function toggleCluster(clusterId: string) {
    setExpandedClusters((prev) => {
      const out = new Set(prev);
      if (out.has(clusterId)) out.delete(clusterId);
      else out.add(clusterId);
      return out;
    });
  }

  function toggleSelected(proposalId: string) {
    setSelectedIds((prev) => {
      const out = new Set(prev);
      if (out.has(proposalId)) out.delete(proposalId);
      else out.add(proposalId);
      return out;
    });
  }

  function doApprove(p: Proposal | null, keyboard: boolean) {
    if (!p) return;
    const tier = selectTier(p, { tenantAutoApprove: {} });
    if (tier === 4) {
      // T4 must go through the modal; clicking the inline ✓ on a T4
      // row shouldn't bypass typed-phrase. Surface a hint.
      toast.message("This proposal needs typed confirmation.", {
        description: "Open it in the detail pane to approve.",
      });
      return;
    }
    if (tier === 3) {
      // T3 requires per-field choices; route to detail pane.
      setFocusedId(p.proposal_id);
      return;
    }
    approve.mutate({
      proposalId: p.proposal_id,
      tier,
      typedPhrase: null,
      keyboardPath: keyboard,
    });
  }

  function doReject(p: Proposal | null, keyboard: boolean) {
    if (!p) return;
    reject.mutate({
      proposalId: p.proposal_id,
      mode: "reject",
      tier: 1,
      keyboardPath: keyboard,
    });
  }

  function doSnooze(p: Proposal | null, until: Date, keyboard: boolean) {
    if (!p) return;
    snooze.mutate({
      proposalId: p.proposal_id,
      snoozeUntil: until,
      reason: "custom",
      keyboardPath: keyboard,
    });
  }

  function doMute(p: Proposal | null) {
    if (!p) return;
    reject.mutate({
      proposalId: p.proposal_id,
      mode: "mute",
      reason: "Muted from inbox",
      suppressionAggregateId: p.entity_id,
      suppressionFieldName: Object.keys(p.payload_after)[0] ?? null,
    });
  }

  function doDismiss(p: Proposal | null) {
    if (!p) return;
    reject.mutate({ proposalId: p.proposal_id, mode: "dismiss_duplicate" });
  }

  function clearSelection() {
    if (selectedIds.size > 0) {
      setSelectedIds(new Set());
    } else if (focusedId) {
      // Esc closes detail when nothing selected
      setFocusedId(null);
    }
  }

  function selectAllVisible() {
    setSelectedIds(new Set(visibleProposals.map((p) => p.proposal_id)));
  }

  // ---- keyboard ----
  useInboxShortcuts({
    j: () => focusDelta(1),
    k: () => focusDelta(-1),
    e: () => {
      if (!focusedProposal) return;
      setExpandedClusters((prev) => {
        const out = new Set(prev);
        out.add(focusedProposal.cluster_id);
        return out;
      });
    },
    y: () => doApprove(focusedProposal, true),
    n: () => doReject(focusedProposal, true),
    h: () => {
      // Open snooze picker for focused proposal, default tomorrow.
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      tomorrow.setHours(9, 0, 0, 0);
      doSnooze(focusedProposal, tomorrow, true);
    },
    m: () => doMute(focusedProposal),
    d: () => doDismiss(focusedProposal),
    esc: () => clearSelection(),
    "select-all": () => selectAllVisible(),
    "command-palette": () => setPaletteOpen(true),
    "focus-mode": () => focusMode.toggle(),
  });

  // ---- bulk computation ----
  const bulkSummary = useMemo(() => {
    const selected = visibleProposals.filter((p) => selectedIds.has(p.proposal_id));
    let eligible = 0;
    let blockedHipaa = 0;
    let blockedT4 = 0;
    let blockedCrossTenant = 0;
    const tenantSet = new Set<string>();
    for (const p of selected) {
      tenantSet.add(p.tenant_id);
      const tier = selectTier(p, { tenantAutoApprove: {} });
      if (tier === 4) blockedT4 += 1;
      else if (p.compliance.hipaa) blockedHipaa += 1;
      else if (p.cross_tenant) blockedCrossTenant += 1;
      else eligible += 1;
    }
    return {
      eligible,
      blockedHipaa,
      blockedT4,
      blockedCrossTenant,
      tenantCount: tenantSet.size,
    };
  }, [visibleProposals, selectedIds]);

  // ---- counts for nav ----
  const totals = useMemo(() => {
    const today = visibleProposals.length;
    return { today, approved: 0, rejected: 0, snoozed: 0 };
  }, [visibleProposals]);

  // ---- detail pane ----
  const fieldChoices = focusedProposal ? fieldChoicesByProposal[focusedProposal.proposal_id] ?? {} : {};
  const diffEntries: StructuredFieldEntry[] = focusedProposal
    ? entriesFromPayload(focusedProposal.payload_before, focusedProposal.payload_after)
    : [];
  const diffReady = !focusedProposal
    ? true
    : diffEntries.every((e) => {
        if (e.masterValue === e.proposedValue) return true;
        return fieldChoices[e.field] !== undefined || e.agentRecommendation != null;
      });

  // ---- dynamic agent slugs from the current inbox list ----
  const agentSlugs = useMemo(() => {
    const slugs = new Set<string>();
    for (const p of allProposalsRaw) {
      if (p.agent_id && p.agent_id !== "unknown") slugs.add(p.agent_id);
    }
    return Array.from(slugs).sort();
  }, [allProposalsRaw]);

  // ---- command palette actions ----
  const paletteActions = useMemo(
    () => {
      const agentFilterEntries = agentSlugs.map((slug) => ({
        id: `agent-filter-${slug}`,
        group: "Filter" as const,
        label: `Filter by ${slug}`,
        agentSlug: slug,
        onRun: () => {
          const url = new URL(window.location.href);
          url.searchParams.set("agent", slug);
          window.history.replaceState({}, "", url);
        },
      }));

      return [
        { id: "approve", group: "Actions" as const, label: "Approve focused", shortcut: "Y", onRun: () => doApprove(focusedProposal, false), hidden: !focusedProposal },
        { id: "reject", group: "Actions" as const, label: "Reject focused", shortcut: "N", onRun: () => doReject(focusedProposal, false), hidden: !focusedProposal },
        { id: "mute", group: "Actions" as const, label: "Mute agent for this entity", shortcut: "M", onRun: () => doMute(focusedProposal), hidden: !focusedProposal },
        { id: "dismiss", group: "Actions" as const, label: "Dismiss as duplicate", shortcut: "D", onRun: () => doDismiss(focusedProposal), hidden: !focusedProposal },
        { id: "nav-review", group: "Navigate" as const, label: "Jump to needs-review", onRun: () => setView("needs-review") },
        { id: "nav-snoozed", group: "Navigate" as const, label: "Jump to snoozed", onRun: () => setView("snoozed") },
        { id: "nav-resolved", group: "Navigate" as const, label: "Jump to recently resolved", onRun: () => setView("resolved") },
        { id: "focus-mode", group: "View" as const, label: focusMode.enabled ? "Disable Focus Mode" : "Enable Focus Mode", shortcut: "cmd+.", onRun: () => focusMode.toggle() },
        { id: "hipaa-only", group: "Filter" as const, label: "HIPAA only", onRun: () => {
          const url = new URL(window.location.href);
          url.searchParams.set("hipaa_only", "true");
          window.history.replaceState({}, "", url);
        }},
        ...agentFilterEntries,
        { id: "settings-auto", group: "Settings" as const, label: "Open auto-approve thresholds", onRun: () => navigate({ to: "/settings" }) },
      ];
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [focusedProposal, focusMode, navigate, agentSlugs],
  );

  // ---- render ----
  // LOCAL VARIANT: /inbox needs the full post-sidebar width for triage density;
  // the shared AppShell max-width is tuned for CRUD curation screens.
  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col bg-background lg:relative lg:left-1/2 lg:w-[calc(100vw-16rem-3rem)] lg:-translate-x-1/2">
      <InboxHeader
        view={view}
        onViewChange={setView}
        focusMode={focusMode.enabled}
        onToggleFocusMode={focusMode.toggle}
        totals={totals}
        conflictsOnly={conflictsOnly}
        onToggleConflicts={() => setConflictsOnly((v) => !v)}
      />

      {/* Desktop 3-pane */}
      <div className="hidden flex-1 overflow-hidden lg:flex">
        <ResizablePanelGroup orientation="horizontal">
          <ResizablePanel defaultSize={PANE_DEFAULTS.nav} minSize="12%" maxSize="28%">
            <NavSidebar
              view={view}
              onViewChange={setView}
              totals={totals}
            />
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={PANE_DEFAULTS.list} minSize="28%">
            <ListPane
              clusters={hydrated}
              focusedProposalId={focusedId}
              selectedProposalIds={selectedIds}
              expandedClusters={expandedClusters}
              onToggleCluster={toggleCluster}
              onSelectProposal={(p) => setFocusedId(p.proposal_id)}
              onFocusProposal={(p) => setFocusedId(p.proposal_id)}
              onQuickApprove={(p) => doApprove(p, false)}
              onQuickReject={(p) => doReject(p, false)}
              onMultiSelect={(p) => toggleSelected(p.proposal_id)}
              isLoading={list.isPending}
              hasNextPage={Boolean(list.hasNextPage)}
              onLoadMore={() => list.fetchNextPage()}
              emptyState={
                visibleProposals.length === 0 && !list.isPending ? (
                  <EmptyStateInboxZero
                    coldStart={allProposalsRaw.length === 0}
                    stats={{
                      today: 0,
                      approved: 0,
                      rejected: 0,
                      snoozed: 0,
                      topAgentToday: null,
                      bestCalibratedAgent: null,
                      bestCalibratedRate: null,
                      nextBatchEtaMinutes: null,
                    }}
                    onReviewSnoozed={() => setView("snoozed")}
                    onOpenAgentReport={() => navigate({ to: "/settings" })}
                    onOpenAutoApproveSettings={() => navigate({ to: "/settings" })}
                  />
                ) : null
              }
            />
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={PANE_DEFAULTS.detail} minSize="28%">
            <DetailPane
              proposal={focusedProposal}
              tier={focusedTier}
              diffEntries={diffEntries}
              fieldChoices={fieldChoices}
              diffReady={diffReady}
              busy={approve.isPending || reject.isPending || snooze.isPending}
              onFieldChoice={(field, choice) => {
                if (!focusedProposal) return;
                setFieldChoicesByProposal((prev) => ({
                  ...prev,
                  [focusedProposal.proposal_id]: {
                    ...(prev[focusedProposal.proposal_id] ?? {}),
                    [field]: choice,
                  },
                }));
              }}
              onApprove={(typed) => {
                if (!focusedProposal) return;
                const tier = selectTier(focusedProposal, { tenantAutoApprove: {} });
                approve.mutate({
                  proposalId: focusedProposal.proposal_id,
                  tier,
                  typedPhrase: typed,
                  fieldChoices: fieldChoices as Record<string, "master" | "proposed" | "custom">,
                  keyboardPath: false,
                });
              }}
              onReject={() => doReject(focusedProposal, false)}
              onSnooze={(until) => doSnooze(focusedProposal, until, false)}
              onOfferSnooze={() => {
                // Snooze 1h from now as a default for the "did you mean it?" path
                const d = new Date();
                d.setHours(d.getHours() + 1);
                doSnooze(focusedProposal, d, false);
              }}
            />
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>

      {/* Mobile single-pane (≤ lg) */}
      <div className="co-scrollbar flex-1 overflow-y-auto p-co-12 lg:hidden">
        {visibleProposals.length === 0 && !list.isPending ? (
          <EmptyStateInboxZero
            coldStart={allProposalsRaw.length === 0}
            stats={{ today: 0, approved: 0, rejected: 0, snoozed: 0 }}
          />
        ) : (
          <MobileList
            clusters={hydrated}
            expandedClusters={expandedClusters}
            onToggleCluster={toggleCluster}
            onApprove={(p) => doApprove(p, false)}
            onReject={(p) => doReject(p, false)}
            onSnoozeDefault={(p) => {
              const d = new Date();
              d.setDate(d.getDate() + 1);
              d.setHours(9, 0, 0, 0);
              doSnooze(p, d, false);
            }}
            mobileSwipe
          />
        )}
      </div>

      <BulkActionBar
        selectionCount={selectedIds.size}
        eligibleCount={bulkSummary.eligible}
        blockedHipaa={bulkSummary.blockedHipaa}
        blockedTier4={bulkSummary.blockedT4}
        blockedCrossTenant={bulkSummary.blockedCrossTenant}
        tenantSlugs={Array.from({ length: bulkSummary.tenantCount }, (_, i) => `t${i + 1}`)}
        busy={bulkApprove.isPending || bulkReject.isPending}
        onApproveAll={() => {
          const eligibleIds = visibleProposals
            .filter((p) => selectedIds.has(p.proposal_id))
            .filter((p) => {
              const tier = selectTier(p, { tenantAutoApprove: {} });
              return tier <= 2 && !p.compliance.hipaa && !p.cross_tenant;
            })
            .map((p) => p.proposal_id);
          if (eligibleIds.length === 0) return;
          if (eligibleIds.length > 10) {
            setBulkConfirmCount(eligibleIds.length);
            setBulkConfirmOpen(true);
            return;
          }
          bulkApprove.mutate({ proposalIds: eligibleIds, tier: 1 });
        }}
        onRejectAll={() => {
          const ids = Array.from(selectedIds);
          if (ids.length === 0) return;
          bulkReject.mutate({ proposalIds: ids, reason: "Bulk reject from inbox" });
        }}
        onSnoozeAll={() => {
          const ids = Array.from(selectedIds);
          if (ids.length === 0) return;
          const tomorrow = new Date();
          tomorrow.setDate(tomorrow.getDate() + 1);
          tomorrow.setHours(9, 0, 0, 0);
          for (const id of ids) {
            snooze.mutate({ proposalId: id, snoozeUntil: tomorrow, reason: "tomorrow" });
          }
        }}
        onClear={() => setSelectedIds(new Set())}
      />

      {/* Bulk typed-phrase confirm modal (>10 items) */}
      <TypedPhraseConfirm
        open={bulkConfirmOpen}
        onOpenChange={setBulkConfirmOpen}
        expectedPhrase={`approve ${bulkConfirmCount} items`}
        policyExplanation={`Approve ${bulkConfirmCount} proposals at once.`}
        policyDetail="Bulk approval applies to all eligible (T1/T2, non-HIPAA, non-cross-tenant) selected proposals."
        busy={bulkApprove.isPending}
        onConfirm={(typed) => {
          const eligibleIds = visibleProposals
            .filter((p) => selectedIds.has(p.proposal_id))
            .filter((p) => {
              const tier = selectTier(p, { tenantAutoApprove: {} });
              return tier <= 2 && !p.compliance.hipaa && !p.cross_tenant;
            })
            .map((p) => p.proposal_id);
          if (eligibleIds.length === 0) return;
          bulkApprove.mutate({ proposalIds: eligibleIds, typedPhrase: typed, tier: 1 });
        }}
        onOfferSnooze={() => {
          const ids = Array.from(selectedIds);
          if (ids.length === 0) return;
          const tomorrow = new Date();
          tomorrow.setDate(tomorrow.getDate() + 1);
          tomorrow.setHours(9, 0, 0, 0);
          for (const id of ids) {
            snooze.mutate({ proposalId: id, snoozeUntil: tomorrow, reason: "tomorrow" });
          }
        }}
      />

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        actions={paletteActions}
      />
    </div>
  );
}

// ---- subcomponents (kept in-file to avoid 14 stub files) ----

type HeaderProps = {
  view: View;
  onViewChange: (v: View) => void;
  focusMode: boolean;
  onToggleFocusMode: () => void;
  totals: { today: number; approved: number; rejected: number; snoozed: number };
  conflictsOnly: boolean;
  onToggleConflicts: () => void;
};

function InboxHeader({ view, onViewChange, focusMode, onToggleFocusMode, totals, conflictsOnly, onToggleConflicts }: HeaderProps) {
  void view;
  void onViewChange;
  const handleSendToDesktop = useCallback(() => {
    const url = import.meta.env.VITE_DESKTOP_HANDOFF_URL;
    if (!url) return;
    const currentUrl = window.location.href;
    window.open(`${url}?url=${encodeURIComponent(currentUrl)}`, "_blank");
  }, []);

  return (
    <header className="flex flex-wrap items-center gap-co-12 border-b border-border bg-background/95 px-co-16 py-co-8 shadow-[var(--shadow-1)]">
      <span className="inline-flex flex-wrap items-center gap-co-6 text-13 text-muted-foreground">
        <MonoNumeric tone="muted">{totals.today}</MonoNumeric>
        <span>today</span>
        <span>/</span>
        <MonoNumeric tone="muted">{totals.approved}</MonoNumeric>
        <span>approved</span>
        <span>/</span>
        <MonoNumeric tone="muted">{totals.rejected}</MonoNumeric>
        <span>rejected</span>
        <span>/</span>
        <MonoNumeric tone="muted">{totals.snoozed}</MonoNumeric>
        <span>snoozed</span>
      </span>
      <div className="hidden items-center gap-co-4 text-muted-foreground xl:flex">
        <KeyboardHint keys="J" label="Next proposal" />
        <KeyboardHint keys="K" label="Previous proposal" />
        <KeyboardHint keys="Y" label="Approve focused proposal" />
        <KeyboardHint keys="N" label="Reject focused proposal" />
        <KeyboardHint keys="cmd+k" label="Open command palette" />
      </div>
      <div className="ml-auto flex items-center gap-co-8">
        <DensityControl className="hidden xl:inline-flex" />
        {import.meta.env.VITE_DESKTOP_HANDOFF_URL && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleSendToDesktop}
            title="Send link to desktop app"
          >
            <Monitor className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Desktop</span>
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className={cn("gap-co-6", conflictsOnly && "bg-warning/10 text-warning")}
          onClick={onToggleConflicts}
          title="Filter: conflicts only"
          aria-pressed={conflictsOnly}
        >
          <AlertTriangle className="h-3.5 w-3.5 text-warning" />
          <span className="hidden sm:inline">Conflicts</span>
        </Button>
        <FocusModeToggle enabled={focusMode} onToggle={onToggleFocusMode} />
      </div>
    </header>
  );
}

type NavSidebarProps = {
  view: View;
  onViewChange: (v: View) => void;
  totals: { today: number };
};

function NavSidebar({ view, onViewChange, totals }: NavSidebarProps) {
  const items: { id: View; label: string; count: number }[] = [
    { id: "needs-review", label: "Needs Review", count: totals.today },
    { id: "snoozed", label: "Snoozed", count: 0 },
    { id: "resolved", label: "Recently Resolved", count: 0 },
  ];
  return (
    <nav className="co-scrollbar h-full overflow-y-auto border-r border-border bg-card/85 p-co-8" aria-label="Inbox sections">
      <ul className="space-y-co-4">
        {items.map((it) => (
          <li key={it.id}>
            <button
              type="button"
              onClick={() => onViewChange(it.id)}
              className={cn(
                "focus-ring flex w-full items-center justify-between rounded-md px-co-10 py-co-8 text-13",
                "transition-colors duration-150 ease-snappy hover:bg-muted",
                view === it.id && "bg-primary/10 font-medium text-primary",
              )}
              aria-current={view === it.id ? "page" : undefined}
            >
              <span>{it.label}</span>
              <Badge variant="outline" className={cn("rounded-[var(--radius-sm)] px-co-6 font-mono text-11", view === it.id && "border-primary/30")}>
                <MonoNumeric tone="muted">{it.count}</MonoNumeric>
              </Badge>
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-co-24 space-y-co-4">
        <p className="px-co-10 font-mono text-11 font-semibold uppercase text-muted-foreground">
          Saved views
        </p>
        <p className="px-co-10 text-12 text-muted-foreground italic">
          (local) coming in 3.4
        </p>
      </div>
    </nav>
  );
}

type ListPaneProps = {
  clusters: ReturnType<typeof hydrateClusters>;
  focusedProposalId: string | null;
  selectedProposalIds: Set<string>;
  expandedClusters: Set<string>;
  onToggleCluster: (clusterId: string) => void;
  onSelectProposal: (p: Proposal) => void;
  onFocusProposal: (p: Proposal) => void;
  onQuickApprove: (p: Proposal) => void;
  onQuickReject: (p: Proposal) => void;
  onMultiSelect: (p: Proposal) => void;
  isLoading: boolean;
  hasNextPage: boolean;
  onLoadMore: () => void;
  emptyState: React.ReactNode;
};

function ListPane({
  clusters,
  focusedProposalId,
  selectedProposalIds,
  expandedClusters,
  onToggleCluster,
  onSelectProposal,
  onFocusProposal,
  onQuickApprove,
  onQuickReject,
  onMultiSelect,
  isLoading,
  hasNextPage,
  onLoadMore,
  emptyState,
}: ListPaneProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: clusters.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 88,
    overscan: 4,
  });

  // Infinite-scroll trigger
  useEffect(() => {
    if (!hasNextPage) return;
    const items = virtualizer.getVirtualItems();
    const last = items.at(-1);
    if (last && last.index >= clusters.length - 5) {
      onLoadMore();
    }
  }, [virtualizer, clusters.length, hasNextPage, onLoadMore]);

  void onMultiSelect; // wired in mobile path; kept here for parity if shift-click is added later

  if (clusters.length === 0) {
    return (
      <div ref={scrollRef} className="co-scrollbar h-full overflow-y-auto p-co-16" aria-live="polite">
        {isLoading ? (
          <p className="flex items-center gap-co-8 text-13 text-muted-foreground">
            <Spinner size="sm" />
            <span>Loading proposals...</span>
          </p>
        ) : (
          emptyState
        )}
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="co-scrollbar h-full overflow-y-auto p-co-8" aria-live="polite">
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((vi) => {
          const cluster = clusters[vi.index];
          if (!cluster) return null;
          return (
            <div
              key={cluster.cluster_id}
              ref={virtualizer.measureElement}
              data-index={vi.index}
              style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${vi.start}px)` }}
            >
              <div className="mb-co-8">
                <ClusterCard
                  cluster={cluster}
                  proposals={cluster.proposals}
                  expanded={expandedClusters.has(cluster.cluster_id)}
                  onToggle={() => onToggleCluster(cluster.cluster_id)}
                  focusedProposalId={focusedProposalId}
                  selectedProposalIds={selectedProposalIds}
                  onSelectProposal={onSelectProposal}
                  onFocusProposal={onFocusProposal}
                  onQuickApprove={onQuickApprove}
                  onQuickReject={onQuickReject}
                  onOpenDetail={() => {
                    const first = cluster.proposals[0];
                    if (first) onSelectProposal(first);
                  }}
                  hasConflict={cluster.hasConflict}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

type DetailPaneProps = {
  proposal: Proposal | null;
  tier: ReturnType<typeof selectTier>;
  diffEntries: StructuredFieldEntry[];
  fieldChoices: Record<string, FieldChoice>;
  diffReady: boolean;
  busy: boolean;
  onFieldChoice: (field: string, choice: FieldChoice) => void;
  onApprove: (typedPhrase: string | null) => void;
  onReject: () => void;
  onSnooze: (until: Date) => void;
  onOfferSnooze: () => void;
};

function confidenceFillClass(confidence: number) {
  if (confidence >= 0.9) return "bg-confidence-emerald";
  if (confidence >= 0.75) return "bg-confidence-sky";
  if (confidence >= 0.5) return "bg-confidence-amber";
  return "bg-confidence-rose";
}

function DetailPane({
  proposal,
  tier,
  diffEntries,
  fieldChoices,
  diffReady,
  busy,
  onFieldChoice,
  onApprove,
  onReject,
  onSnooze,
  onOfferSnooze,
}: DetailPaneProps) {
  if (!proposal) {
    return (
      <div className="flex h-full items-center justify-center bg-card/35 p-co-32 text-center text-13 text-muted-foreground">
        <div className="space-y-co-8">
          <Bell className="mx-auto h-8 w-8 text-muted-foreground" aria-hidden="true" />
          <p className="font-medium text-foreground">Select a proposal to review.</p>
          <p className="flex flex-wrap justify-center gap-co-4 text-12">
            <KeyboardHint keys="J" label="Next proposal" />
            <KeyboardHint keys="K" label="Previous proposal" />
            <KeyboardHint keys="Y" label="Approve focused proposal" />
            <KeyboardHint keys="N" label="Reject focused proposal" />
            <KeyboardHint keys="cmd+k" label="Open command palette" />
          </p>
        </div>
      </div>
    );
  }

  const subScore = (proposal.confidence ?? 0) * 100;
  const agentLabel = `${proposal.agent_id} ${proposal.agent_version}`;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-card/55">
      <header className="flex items-start gap-co-10 border-b border-border bg-card/85 p-co-12">
        <AgentBadge slug={proposal.agent_id} label={agentLabel} size="sm" />
        <div className="min-w-0 flex-1">
          <span className="block truncate text-14 font-semibold text-foreground" title={proposal.entity_display_name}>
            {proposal.entity_display_name}
          </span>
          <ActionAttribution
            actor={{ type: "agent", slug: proposal.agent_id, label: agentLabel }}
            verb="Proposed"
            timestamp={proposal.created_at}
            target={`T${tier}`}
            compact
            className="mt-co-2"
          />
        </div>
        {proposal.trace_id && (
          <a
            href={`#trace-${proposal.trace_id}`}
            className="inline-flex items-center gap-co-4 rounded-[var(--radius-sm)] text-12 text-primary hover:underline focus-ring"
            title="Open in Laminar"
            aria-label="Open in Laminar"
          >
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </header>
      <div className="co-scrollbar flex-1 space-y-co-16 overflow-y-auto p-co-16">
        <div className="space-y-co-6">
          <div className="flex items-center justify-between text-12 text-muted-foreground">
            <span>Confidence</span>
            <ConfidenceIndicator value={proposal.confidence} mode="numeric" showLabel size="sm" />
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={cn("h-full transition-all duration-300 ease-gentle", confidenceFillClass(proposal.confidence))}
              style={{ width: `${subScore}%` }}
              aria-hidden="true"
            />
          </div>
        </div>

        <section>
          <p className="mb-co-4 font-mono text-11 font-medium uppercase text-muted-foreground">
            What the agent proposes
          </p>
          <p className="rounded-md border border-border bg-muted/35 p-co-10 text-13 italic text-foreground">
            {proposal.rationale}
          </p>
        </section>

        <section>
          <p className="mb-co-4 font-mono text-11 font-medium uppercase text-muted-foreground">
            Structured changes
          </p>
          <StructuredDiff
            fields={diffEntries}
            choices={fieldChoices}
            perFieldChoice={tier === 3}
            onChange={onFieldChoice}
          />
        </section>

        <EvidencePanel proposalId={proposal.proposal_id} defaultExpanded={tier === 2} />

        {proposal.case_file_links.length > 0 && (
          <section className="rounded-md border border-warning/40 bg-warning/10 p-co-12 text-12">
            <p className="mb-co-4 font-medium text-foreground">Related work</p>
            <ul className="space-y-co-4">
              {proposal.case_file_links.map((l) => (
                <li key={`${l.project_id}-${l.task_id ?? "root"}`}>
                  <a
                    href={`https://projects.magicunicorn.dev/projects/${l.project_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-co-4 text-primary hover:underline"
                  >
                    <ExternalLink className="h-3 w-3" />
                    {l.project_name}
                    {l.task_name && ` / ${l.task_name}`}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <footer className="flex flex-wrap items-center justify-end gap-co-8 border-t border-border bg-card/95 p-co-12 shadow-[var(--shadow-2)] backdrop-blur">
        <Button variant="ghost" size="sm" onClick={onReject} disabled={busy}>
          Reject
          <KeyboardHint keys="N" label="Reject focused proposal" />
        </Button>
        <SnoozePicker onSnooze={(until) => onSnooze(until)} busy={busy} />
        <TieredApproveButton
          proposal={proposal}
          tier={tier}
          diffReady={diffReady}
          busy={busy}
          onApprove={onApprove}
          onOfferSnooze={onOfferSnooze}
        />
      </footer>
    </div>
  );
}

// ---- mobile single-pane ----

type MobileListProps = {
  clusters: ReturnType<typeof hydrateClusters>;
  expandedClusters: Set<string>;
  onToggleCluster: (clusterId: string) => void;
  onApprove: (p: Proposal) => void;
  onReject: (p: Proposal) => void;
  onSnoozeDefault: (p: Proposal) => void;
  mobileSwipe?: boolean;
};

function MobileList({
  clusters,
  expandedClusters,
  onToggleCluster,
  onApprove,
  onReject,
  onSnoozeDefault,
  mobileSwipe = false,
}: MobileListProps) {
  void onSnoozeDefault; // wired via swipe gestures
  return (
    <div className="space-y-3">
      {clusters.map((cluster) => (
        <ClusterCard
            key={cluster.cluster_id}
            cluster={cluster}
            proposals={cluster.proposals}
            expanded={expandedClusters.has(cluster.cluster_id)}
            onToggle={() => onToggleCluster(cluster.cluster_id)}
            mobileSwipe={mobileSwipe}
          onQuickApprove={(p) => {
            const tier = selectTier(p, { tenantAutoApprove: {} });
            if (tier >= 3 || p.compliance.hipaa) {
              toast.message("This proposal requires desktop.", {
                description: "Tier-4/dedup decisions need the desktop UI.",
              });
              return;
            }
            onApprove(p);
          }}
          onQuickReject={(p) => onReject(p)}
        />
      ))}
    </div>
  );
}

// Kept for the help screen referenced by Cmd+K palette in a future variant.
void INBOX_SHORTCUT_DESCRIPTIONS;
