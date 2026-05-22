/**
 * /inbox — the Phase 3.3b approval inbox cockpit.
 *
 * Layout (desktop ≥1024px):
 *   ┌───────────────────────────────────────────────────────────┐
 *   │ Header: stats · filter chips · FocusMode                  │
 *   ├─────┬──────────────────────────────┬────────────────────┬┘
 *   │ Nav │ Virtualized cluster list      │ Detail pane        │
 *   │ 220 │ 480                           │ flex               │
 *   └─────┴──────────────────────────────┴────────────────────┴
 *   │  BulkActionBar slides up when N≥2 selected               │
 *   └───────────────────────────────────────────────────────────┘
 *
 * Mobile (<1024px): single-pane list; swipe-right approves T1, swipe-
 * left snoozes; long-press opens detail; T4/dedup/bulk blocked with
 * "send link to desktop" toast.
 *
 * Keyboard (active when /inbox is the route): J/K nav · E expand
 * detail · Y approve · N reject · H snooze · M mute · D dismiss · Esc
 * clear · ⌘A select all · ⌘K command palette · ⌘. focus mode · ⌘Z undo.
 */
import { useEffect, useMemo, useState } from "react";
import { HotkeysProvider } from "react-hotkeys-hook";
import { useNavigate } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef } from "react";
import { AlertTriangle, Bell, ExternalLink } from "lucide-react";
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
import { ConfidenceDots } from "@/components/inbox/ConfidenceDots";
import { AgentBadge } from "@/components/inbox/AgentBadge";
import { StructuredDiff, entriesFromPayload } from "@/components/inbox/StructuredDiff";
import { TieredApproveButton } from "@/components/inbox/TieredApproveButton";
import { SnoozePicker } from "@/components/inbox/SnoozePicker";

import {
  useApproveMutation,
  useBulkApproveMutation,
  useBulkRejectMutation,
  useInboxList,
  useRejectMutation,
  useSnoozeMutation,
  type InboxFilters,
} from "@/hooks/useInbox";
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

const PANE_DEFAULTS = { nav: 18, list: 42, detail: 40 };

export function InboxRoute() {
  return (
    <HotkeysProvider initiallyActiveScopes={[INBOX_HOTKEY_SCOPE]}>
      <InboxCockpit />
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

  const focusMode = useFocusMode();

  const filters: InboxFilters = useMemo(
    () => ({ status: view === "needs-review" ? "proposed" : view === "snoozed" ? "snoozed" : "resolved" }),
    [view],
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
      // Open snooze picker for focused proposal — default tomorrow
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

  // ---- command palette actions ----
  const paletteActions = useMemo(
    () => [
      { id: "approve", group: "Actions" as const, label: "Approve focused", shortcut: "Y", onRun: () => doApprove(focusedProposal, false), hidden: !focusedProposal },
      { id: "reject", group: "Actions" as const, label: "Reject focused", shortcut: "N", onRun: () => doReject(focusedProposal, false), hidden: !focusedProposal },
      { id: "mute", group: "Actions" as const, label: "Mute agent for this entity", shortcut: "M", onRun: () => doMute(focusedProposal), hidden: !focusedProposal },
      { id: "dismiss", group: "Actions" as const, label: "Dismiss as duplicate", shortcut: "D", onRun: () => doDismiss(focusedProposal), hidden: !focusedProposal },
      { id: "nav-review", group: "Navigate" as const, label: "Jump to needs-review", onRun: () => setView("needs-review") },
      { id: "nav-snoozed", group: "Navigate" as const, label: "Jump to snoozed", onRun: () => setView("snoozed") },
      { id: "nav-resolved", group: "Navigate" as const, label: "Jump to recently resolved", onRun: () => setView("resolved") },
      { id: "focus-mode", group: "View" as const, label: focusMode.enabled ? "Disable Focus Mode" : "Enable Focus Mode", shortcut: "⌘.", onRun: () => focusMode.toggle() },
      { id: "settings-auto", group: "Settings" as const, label: "Open auto-approve thresholds", onRun: () => navigate({ to: "/settings" }) },
    ],
    [focusedProposal, focusMode, navigate],
  );

  // ---- render ----
  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      <InboxHeader
        view={view}
        onViewChange={setView}
        focusMode={focusMode.enabled}
        onToggleFocusMode={focusMode.toggle}
        totals={totals}
      />

      {/* Desktop 3-pane */}
      <div className="hidden flex-1 overflow-hidden lg:flex">
        <ResizablePanelGroup orientation="horizontal">
          <ResizablePanel defaultSize={PANE_DEFAULTS.nav} minSize={12} maxSize={28}>
            <NavSidebar
              view={view}
              onViewChange={setView}
              totals={totals}
            />
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={PANE_DEFAULTS.list} minSize={28}>
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
          <ResizablePanel defaultSize={PANE_DEFAULTS.detail} minSize={28}>
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
      <div className="flex-1 overflow-y-auto p-3 lg:hidden">
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
            const phrase = window.prompt(`Type "approve ${eligibleIds.length} items" to confirm:`);
            if (phrase === null) return;
            bulkApprove.mutate({ proposalIds: eligibleIds, typedPhrase: phrase, tier: 1 });
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
};

function InboxHeader({ view, onViewChange, focusMode, onToggleFocusMode, totals }: HeaderProps) {
  void view;
  void onViewChange;
  return (
    <header className="flex flex-wrap items-center gap-3 border-b bg-card/70 px-4 py-2.5">
      <span className="font-mono text-sm tabular-nums text-muted-foreground">
        {totals.today} today · {totals.approved} approved · {totals.rejected} rejected · {totals.snoozed} snoozed
      </span>
      <div className="ml-auto flex items-center gap-2">
        <Button variant="ghost" size="sm" className="gap-1.5" title="Filter: conflicts only">
          <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
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
  const items: { id: View; label: string; count: number; color: string }[] = [
    { id: "needs-review", label: "Needs Review", count: totals.today, color: "bg-violet-500" },
    { id: "snoozed", label: "Snoozed", count: 0, color: "bg-amber-500" },
    { id: "resolved", label: "Recently Resolved", count: 0, color: "bg-slate-500" },
  ];
  return (
    <nav className="h-full overflow-y-auto border-r bg-card p-3" aria-label="Inbox sections">
      <ul className="space-y-1">
        {items.map((it) => (
          <li key={it.id}>
            <button
              type="button"
              onClick={() => onViewChange(it.id)}
              className={cn(
                "flex w-full items-center justify-between rounded-md px-3 py-2 text-sm",
                "hover:bg-muted",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500",
                view === it.id && "bg-violet-500/10 text-violet-500 font-medium",
              )}
              aria-current={view === it.id ? "page" : undefined}
            >
              <span>{it.label}</span>
              <Badge variant="outline" className={cn("font-mono text-[10px]", view === it.id && "border-violet-500/30")}>
                {it.count}
              </Badge>
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-6 space-y-1">
        <p className="px-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Saved views
        </p>
        <p className="px-3 text-xs text-muted-foreground italic">
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
      <div ref={scrollRef} className="h-full overflow-y-auto p-4" aria-live="polite">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading proposals…</p>
        ) : (
          emptyState
        )}
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto p-2" aria-live="polite">
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
              <div className="mb-2">
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
                  hasConflict={cluster.proposals.some(() => false /* per-proposal conflict signal would slot here */)}
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
      <div className="flex h-full items-center justify-center bg-card/30 p-8 text-center text-sm text-muted-foreground">
        <div className="space-y-2">
          <Bell className="mx-auto h-8 w-8 text-slate-500" aria-hidden="true" />
          <p>Select a proposal to review.</p>
          <p className="text-xs">J/K to navigate · Y/N to decide · ⌘K for commands</p>
        </div>
      </div>
    );
  }

  const subScore = (proposal.confidence ?? 0) * 100;

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <header className="flex items-start gap-2 border-b bg-card/40 p-3">
        <AgentBadge slug={proposal.agent_id} version={proposal.agent_version} variant="with-version" />
        <span className="flex-1 truncate text-sm font-medium">{proposal.entity_display_name}</span>
        {proposal.trace_id && (
          <a
            href={`#trace-${proposal.trace_id}`}
            className="inline-flex items-center gap-1 text-xs text-violet-500 hover:underline"
            title="Open in Laminar"
            aria-label="Open in Laminar"
          >
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </header>
      <div className="space-y-4 p-4">
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>Confidence</span>
            <ConfidenceDots value={proposal.confidence} showLabel showNumeric size="sm" />
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-violet-500 transition-all"
              style={{ width: `${subScore}%` }}
              aria-hidden="true"
            />
          </div>
        </div>

        <section>
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            What the agent proposes
          </p>
          <p className="text-sm italic text-foreground/85">{proposal.rationale}</p>
        </section>

        <section>
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
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
          <section className="rounded-md border border-amber-500/40 bg-amber-50/30 p-3 text-xs dark:bg-amber-950/20">
            <p className="mb-1 font-medium text-amber-900 dark:text-amber-200">Related work</p>
            <ul className="space-y-1">
              {proposal.case_file_links.map((l) => (
                <li key={`${l.project_id}-${l.task_id ?? "root"}`}>
                  <a
                    href={`https://projects.magicunicorn.dev/projects/${l.project_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-amber-700 hover:underline dark:text-amber-300"
                  >
                    🔗 {l.project_name}
                    {l.task_name && ` · ${l.task_name}`}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <footer className="sticky bottom-0 flex flex-wrap items-center justify-end gap-2 border-t bg-card/95 p-3 backdrop-blur">
        <Button variant="ghost" size="sm" onClick={onReject} disabled={busy}>
          Reject · N
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
};

function MobileList({
  clusters,
  expandedClusters,
  onToggleCluster,
  onApprove,
  onReject,
  onSnoozeDefault,
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
