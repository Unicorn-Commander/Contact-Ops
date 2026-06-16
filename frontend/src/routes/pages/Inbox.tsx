/**
 * /review - the Phase 3.3b Review Queue cockpit.
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
 * Keyboard (active when /review is the route): J/K nav · E expand
 * detail · Y approve · N reject · H snooze · M mute · D dismiss · Esc
 * clear, Cmd+A select all, Cmd+K command palette, Cmd+. focus mode, Cmd+Z undo.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { HotkeysProvider } from "react-hotkeys-hook";
import { useAuth } from "react-oidc-context";
import { useNavigate } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { AlertTriangle, Bell, ExternalLink, ListChecks, Monitor } from "lucide-react";
import { toast } from "sonner";

import { tools } from "@/lib/mcp";
import { env } from "@/lib/env";

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
import { BulkApproveBar } from "@/components/inbox/BulkApproveBar";
import {
  EMPTY_QUALITY_FILTER,
  QualityFilterChips,
  proposalMatchesQualityFilter,
  qualityFilterIsEmpty,
  type ConnectorProviderFilter,
  type QualityFilterState
} from "@/components/inbox/QualityFilterChips";
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
import type { RowClickMeta } from "@/components/inbox/ProposalRow";
import type { Proposal } from "@/lib/types";
import { cn } from "@/lib/utils";

type View = "needs-review" | "snoozed" | "resolved";

const PANE_DEFAULTS = { nav: "18%", list: "42%", detail: "40%" };

// A plain (no-modifier) row activation — opens the detail pane.
const NO_MODS: RowClickMeta = { shiftKey: false, metaKey: false, ctrlKey: false };

// ---- URL <-> QualityFilterState (shareable approve-all links) ----

const VALID_PROVIDERS: ConnectorProviderFilter[] = ["gmail", "m365", "icloud"];

function readQualityFilterFromUrl(params: URLSearchParams): QualityFilterState {
  const confRaw = params.get("confMin");
  let confMin: QualityFilterState["confMin"] = null;
  if (confRaw === "0.95") confMin = 0.95;
  else if (confRaw === "0.90" || confRaw === "0.9") confMin = 0.9;
  else if (confRaw === "0.80" || confRaw === "0.8") confMin = 0.8;

  const providerRaw = (params.get("provider") ?? "").split(",").filter(Boolean);
  const providers = providerRaw.filter((p): p is ConnectorProviderFilter =>
    (VALID_PROVIDERS as string[]).includes(p)
  );

  return {
    confMin,
    hasEmail: params.get("hasEmail") === "1",
    hasPhone: params.get("hasPhone") === "1",
    providers
  };
}

function writeQualityFilterToUrl(next: QualityFilterState) {
  const url = new URL(window.location.href);
  if (next.confMin != null) url.searchParams.set("confMin", String(next.confMin));
  else url.searchParams.delete("confMin");
  if (next.hasEmail) url.searchParams.set("hasEmail", "1");
  else url.searchParams.delete("hasEmail");
  if (next.hasPhone) url.searchParams.set("hasPhone", "1");
  else url.searchParams.delete("hasPhone");
  if (next.providers.length > 0) url.searchParams.set("provider", next.providers.join(","));
  else url.searchParams.delete("provider");
  window.history.replaceState({}, "", url);
}

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
  const [qualityFilter, setQualityFilter] = useState<QualityFilterState>(EMPTY_QUALITY_FILTER);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchProgress, setBatchProgress] = useState<{
    settled: number;
    total: number;
    failed: number;
  } | null>(null);

  const focusMode = useFocusMode();
  const auth = useAuth();

  // Selection anchor for shift-range select; last decision for ⌘Z/U undo.
  // Refs (not state) — they steer imperative handlers and must never go stale.
  const anchorIdRef = useRef<string | null>(null);
  const lastDecisionRef = useRef<{ id: string; kind: "approve" | "reject" } | null>(null);

  function readUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const tenantParam = params.get("tenant");
    const agentParam = params.get("agent");
    const hipaaParam = params.get("hipaa_only");
    setTenantIds(tenantParam ? [tenantParam] : undefined);
    setAgentFilter(agentParam);
    setHipaaOnlyFromUrl(hipaaParam === "true");
    setQualityFilter(readQualityFilterFromUrl(params));
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
    () => list.data?.pages.flatMap((p) => p.proposals ?? []) ?? [],
    [list.data],
  );
  const allClustersRaw = useMemo(
    () => list.data?.pages.flatMap((p) => p.clusters ?? []) ?? [],
    [list.data],
  );

  const focusFiltered = useMemo(
    () => applyFocusMode(allProposalsRaw, focusMode.enabled),
    [allProposalsRaw, focusMode.enabled],
  );
  const visibleProposals = useMemo(
    () =>
      qualityFilterIsEmpty(qualityFilter)
        ? focusFiltered
        : focusFiltered.filter((p) => proposalMatchesQualityFilter(p, qualityFilter)),
    [focusFiltered, qualityFilter],
  );
  const qualityMatchedCount = visibleProposals.length;
  const totalAfterFocus = focusFiltered.length;
  const hydrated = useMemo(
    () => hydrateClusters(allClustersRaw, visibleProposals),
    [allClustersRaw, visibleProposals],
  );

  // Canonical top-to-bottom order of rows as rendered (clusters flattened) —
  // the basis for shift-range select and post-decision auto-advance.
  const orderedProposals = useMemo(
    () => hydrated.flatMap((c) => c.proposals),
    [hydrated],
  );
  const orderedIds = useMemo(
    () => orderedProposals.map((p) => p.proposal_id),
    [orderedProposals],
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

  /** Additively select every row between two ids in visual order (Gmail-style). */
  function selectRange(aId: string, bId: string) {
    const ai = orderedIds.indexOf(aId);
    const bi = orderedIds.indexOf(bId);
    if (ai === -1 || bi === -1) return;
    const [lo, hi] = ai <= bi ? [ai, bi] : [bi, ai];
    setSelectedIds((prev) => {
      const out = new Set(prev);
      for (let i = lo; i <= hi; i++) out.add(orderedIds[i]);
      return out;
    });
  }

  // Route a row click by its modifier keys: plain → open detail (focus),
  // ⌘/Ctrl → toggle into multi-select, Shift → range-select from the anchor.
  function handleRowSelect(p: Proposal, meta: RowClickMeta) {
    const id = p.proposal_id;
    if (meta.shiftKey && anchorIdRef.current) {
      selectRange(anchorIdRef.current, id);
      setFocusedId(id);
      return;
    }
    if (meta.metaKey || meta.ctrlKey) {
      toggleSelected(id);
      anchorIdRef.current = id;
      return;
    }
    setFocusedId(id);
    anchorIdRef.current = id;
  }

  // ⌘/Ctrl- or Shift-click on a cluster header selects its proposals — the
  // bulk path for a queue of singleton clusters (rows stay collapsed).
  function handleClusterSelect(proposals: Proposal[], meta: RowClickMeta) {
    const ids = proposals.map((p) => p.proposal_id);
    if (ids.length === 0) return;
    if (meta.shiftKey && anchorIdRef.current) {
      // Cover the whole cluster span relative to the anchor (both ends).
      selectRange(anchorIdRef.current, ids[0]);
      selectRange(anchorIdRef.current, ids[ids.length - 1]);
      return;
    }
    setSelectedIds((prev) => {
      const out = new Set(prev);
      const allSelected = ids.every((id) => out.has(id));
      for (const id of ids) {
        if (allSelected) out.delete(id);
        else out.add(id);
      }
      return out;
    });
    anchorIdRef.current = ids[0];
  }

  // Shift+J/K (or Shift+↑/↓): grow the selection one row from the anchor.
  function extendSelection(delta: 1 | -1) {
    if (orderedIds.length === 0) return;
    if (!anchorIdRef.current) anchorIdRef.current = focusedId ?? orderedIds[0];
    const curIdx = focusedId ? orderedIds.indexOf(focusedId) : -1;
    const nextIdx = Math.max(0, Math.min(orderedIds.length - 1, curIdx + delta));
    const nextId = orderedIds[nextIdx];
    setFocusedId(nextId);
    selectRange(anchorIdRef.current, nextId);
  }

  // The row the cursor should land on after `removedId` leaves the list — the
  // next sibling, else the previous, so keyboard triage flows without a re-aim.
  function nextFocusAfter(removedId: string): string | null {
    const idx = orderedIds.indexOf(removedId);
    if (idx === -1) return null;
    return orderedIds[idx + 1] ?? orderedIds[idx - 1] ?? null;
  }

  // ⌘Z / U — reverse the last decision. Approves reverse cleanly (reject:undo
  // un-applies them); rejects have no server-side inverse, so we say so.
  function undoLast() {
    const last = lastDecisionRef.current;
    if (!last) {
      toast.message("Nothing to undo");
      return;
    }
    if (last.kind === "reject") {
      toast.message("That reject can't be undone here.", {
        description: "Rejected proposals are dismissed; re-run the agent to re-propose.",
      });
      lastDecisionRef.current = null;
      return;
    }
    const token = auth.user?.access_token ?? "";
    tools
      .rejectProposal({ proposal_id: last.id, mode: "undo" }, token)
      .then(() => {
        toast.success("Undone");
        void list.refetch();
      })
      .catch((e: unknown) => {
        const m = e instanceof Error ? e.message : "unknown error";
        toast.error(`Undo failed: ${m}`);
      });
    lastDecisionRef.current = null;
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
    const next = nextFocusAfter(p.proposal_id);
    approve.mutate({
      proposalId: p.proposal_id,
      tier,
      typedPhrase: null,
      keyboardPath: keyboard,
    });
    lastDecisionRef.current = { id: p.proposal_id, kind: "approve" };
    if (next) setFocusedId(next); // auto-advance: keep the triage cadence going
  }

  function doReject(p: Proposal | null, keyboard: boolean) {
    if (!p) return;
    const next = nextFocusAfter(p.proposal_id);
    reject.mutate({
      proposalId: p.proposal_id,
      mode: "reject",
      tier: 1,
      keyboardPath: keyboard,
    });
    lastDecisionRef.current = { id: p.proposal_id, kind: "reject" };
    if (next) setFocusedId(next); // auto-advance
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
      anchorIdRef.current = null;
    } else if (focusedId) {
      // Esc closes detail when nothing selected
      setFocusedId(null);
    }
  }

  function selectAllVisible() {
    setSelectedIds(new Set(visibleProposals.map((p) => p.proposal_id)));
  }

  // ---- quality filter batch (filter-driven bulk approve) ----
  //
  // Runs the existing single-approve / single-reject mutation across every
  // proposal that matches the quality filter, using Promise.allSettled so
  // partial failures don't abort the batch. We collapse all the optimistic-
  // remove churn into a single invalidation at the end.
  async function runQualityBatch(mode: "approve" | "reject") {
    const targets = visibleProposals.slice();
    if (targets.length === 0) return;
    setBatchBusy(true);
    setBatchProgress({ settled: 0, total: targets.length, failed: 0 });

    let settled = 0;
    let failed = 0;

    // Important: don't await each call serially — let them race. We track
    // settlement through .then/.catch on each call instead of .allSettled so
    // the progress count ticks live as each settles.
    const promises = targets.map((p) => {
      const tier = selectTier(p, { tenantAutoApprove: {} });
      const safeTier = tier <= 2 ? tier : 1;
      const work =
        mode === "approve"
          ? approve
              .mutateAsync({
                proposalId: p.proposal_id,
                tier: safeTier,
                typedPhrase: null,
                keyboardPath: true,
              })
              .then(() => undefined)
          : reject
              .mutateAsync({
                proposalId: p.proposal_id,
                mode: "reject",
                tier: safeTier,
                reason: "Bulk reject from quality filter",
                keyboardPath: true,
              })
              .then(() => undefined);

      return work
        .catch((err: unknown) => {
          failed += 1;
          return err;
        })
        .finally(() => {
          settled += 1;
          setBatchProgress({ settled, total: targets.length, failed });
        });
    });

    await Promise.allSettled(promises);

    const verbed = mode === "approve" ? "Approved" : "Rejected";
    const applied = targets.length - failed;
    const suffix =
      failed > 0
        ? `${applied} ${mode === "approve" ? "approved" : "rejected"} (${failed} failed — see Recent activity).`
        : `${applied} ${mode === "approve" ? "proposals" : "proposals rejected"}.`;
    if (failed > 0) {
      toast.warning(`${verbed} ${suffix}`);
    } else {
      toast.success(`${verbed} ${suffix}`);
    }

    // Single refetch — invalidate the inbox query family so the list updates
    // after the batch completes.
    void list.refetch();

    setBatchBusy(false);
    // Keep the final settled-count visible for a moment so users can confirm.
    window.setTimeout(() => setBatchProgress(null), 2000);
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
    undo: () => undoLast(),
    "extend-down": () => extendSelection(1),
    "extend-up": () => extendSelection(-1),
    "toggle-select": () => {
      if (!focusedProposal) return;
      toggleSelected(focusedProposal.proposal_id);
      anchorIdRef.current = focusedProposal.proposal_id;
    },
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
        clusterCount={hydrated.length}
        conflictsOnly={conflictsOnly}
        onToggleConflicts={() => setConflictsOnly((v) => !v)}
      />

      <QualityFilterChips
        value={qualityFilter}
        matchedCount={qualityMatchedCount}
        totalCount={totalAfterFocus}
        onChange={(next) => {
          setQualityFilter(next);
          writeQualityFilterToUrl(next);
        }}
      />

      <BulkApproveBar
        visible={!qualityFilterIsEmpty(qualityFilter)}
        matchedCount={qualityMatchedCount}
        busy={batchBusy}
        progress={batchProgress}
        onApproveAll={() => runQualityBatch("approve")}
        onRejectAll={() => runQualityBatch("reject")}
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
              onSelectProposal={handleRowSelect}
              onSelectCluster={handleClusterSelect}
              onFocusProposal={(p) => setFocusedId(p.proposal_id)}
              onQuickApprove={(p) => doApprove(p, false)}
              onQuickReject={(p) => doReject(p, false)}
              isLoading={list.isPending}
              hasNextPage={Boolean(list.hasNextPage)}
              onLoadMore={() => list.fetchNextPage()}
              emptyState={
                visibleProposals.length === 0 && !list.isPending ? (
                  !qualityFilterIsEmpty(qualityFilter) && focusFiltered.length > 0 ? (
                    <p className="px-co-12 py-co-8 text-13 text-muted-foreground">
                      No proposals match these filters.
                    </p>
                  ) : (
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
                  )
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
          !qualityFilterIsEmpty(qualityFilter) && focusFiltered.length > 0 ? (
            <p className="text-13 text-muted-foreground">
              No proposals match these filters.
            </p>
          ) : (
            <EmptyStateInboxZero
              coldStart={allProposalsRaw.length === 0}
              stats={{ today: 0, approved: 0, rejected: 0, snoozed: 0 }}
            />
          )
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
        matchedTotal={visibleProposals.length}
        onSelectAll={selectAllVisible}
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
  clusterCount: number;
  conflictsOnly: boolean;
  onToggleConflicts: () => void;
};

const VIEW_LABEL: Record<View, string> = {
  "needs-review": "awaiting your review",
  snoozed: "snoozed",
  resolved: "recently resolved",
};

function InboxHeader({ view, onViewChange, focusMode, onToggleFocusMode, totals, clusterCount, conflictsOnly, onToggleConflicts }: HeaderProps) {
  void onViewChange;
  const handleSendToDesktop = useCallback(() => {
    const url = import.meta.env.VITE_DESKTOP_HANDOFF_URL;
    if (!url) return;
    const currentUrl = window.location.href;
    window.open(`${url}?url=${encodeURIComponent(currentUrl)}`, "_blank");
  }, []);

  return (
    <header className="flex flex-wrap items-center gap-co-12 border-b border-border bg-background/95 px-co-16 py-co-8 shadow-[var(--shadow-1)]">
      <span className="inline-flex items-center gap-co-8">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-[oklch(var(--co-brand-300)/0.3)] bg-[oklch(var(--co-brand-500)/0.12)] text-[oklch(var(--co-brand-300))]">
          <ListChecks className="h-4 w-4" strokeWidth={1.8} aria-hidden="true" />
        </span>
        <span className="leading-tight">
          <h1 className="text-14 font-semibold tracking-tight text-foreground">Review Queue</h1>
          <p className="text-11 text-muted-foreground">Agent-proposed changes to your graph</p>
        </span>
      </span>
      <span aria-hidden className="hidden h-7 w-px bg-border sm:block" />
      {/* Live count — the real signal (pending proposals + how many groups they
          fall into). We deliberately do NOT show approved/rejected/snoozed
          tallies here: those aren't computed server-side yet and would read as
          a misleading "0". */}
      <span className="inline-flex flex-wrap items-baseline gap-co-6 text-13 text-muted-foreground" aria-live="polite">
        <MonoNumeric tone="strong">{totals.today}</MonoNumeric>
        <span>{totals.today === 1 ? "proposal" : "proposals"} {VIEW_LABEL[view]}</span>
        {view === "needs-review" && clusterCount > 0 ? (
          <span className="text-muted-foreground">
            · <MonoNumeric tone="muted">{clusterCount}</MonoNumeric> {clusterCount === 1 ? "group" : "groups"}
          </span>
        ) : null}
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
  // Only "needs-review" has a server-computed count today; we show it on that
  // item and leave the others count-less rather than render a misleading "0".
  const items: { id: View; label: string; count: number | null }[] = [
    { id: "needs-review", label: "Needs Review", count: totals.today },
    { id: "snoozed", label: "Snoozed", count: null },
    { id: "resolved", label: "Recently Resolved", count: null },
  ];
  return (
    <nav className="co-scrollbar h-full overflow-y-auto border-r border-border bg-card/85 p-co-8" aria-label="Review Queue sections">
      <p className="px-co-10 pb-co-6 pt-co-4 font-mono text-11 font-semibold uppercase tracking-[0.1em] text-muted-foreground">
        Sections
      </p>
      <ul className="space-y-co-4">
        {items.map((it) => (
          <li key={it.id}>
            <button
              type="button"
              onClick={() => onViewChange(it.id)}
              className={cn(
                "focus-ring flex w-full items-center justify-between rounded-md px-co-10 py-co-8 text-13",
                "transition-colors duration-150 ease-snappy hover:bg-[oklch(var(--co-brand-500)/0.06)]",
                view === it.id && "border border-[oklch(var(--co-brand-300)/0.32)] bg-[oklch(var(--co-brand-500)/0.1)] font-medium text-foreground",
              )}
              aria-current={view === it.id ? "page" : undefined}
            >
              <span>{it.label}</span>
              {it.count != null ? (
                <Badge variant="outline" className={cn("rounded-[var(--radius-sm)] px-co-6 font-mono text-11", view === it.id && "border-[oklch(var(--co-brand-300)/0.4)]")}>
                  <MonoNumeric tone="muted">{it.count}</MonoNumeric>
                </Badge>
              ) : null}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

type ListPaneProps = {
  clusters: ReturnType<typeof hydrateClusters>;
  focusedProposalId: string | null;
  selectedProposalIds: Set<string>;
  expandedClusters: Set<string>;
  onToggleCluster: (clusterId: string) => void;
  onSelectProposal: (p: Proposal, meta: RowClickMeta) => void;
  onSelectCluster: (proposals: Proposal[], meta: RowClickMeta) => void;
  onFocusProposal: (p: Proposal) => void;
  onQuickApprove: (p: Proposal) => void;
  onQuickReject: (p: Proposal) => void;
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
  onSelectCluster,
  onFocusProposal,
  onQuickApprove,
  onQuickReject,
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

  if (clusters.length === 0) {
    return (
      <div ref={scrollRef} className="co-scrollbar h-full overflow-y-auto p-co-16" aria-live="polite">
        {isLoading ? (
          <p className="flex items-center gap-co-8 text-13 text-muted-foreground">
            <Spinner size="sm" label="" />
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
                  onClusterModifierSelect={(meta) => onSelectCluster(cluster.proposals, meta)}
                  onFocusProposal={onFocusProposal}
                  onQuickApprove={onQuickApprove}
                  onQuickReject={onQuickReject}
                  onOpenDetail={() => {
                    const first = cluster.proposals[0];
                    if (first) onSelectProposal(first, NO_MODS);
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

function proposedFieldMap(entries: StructuredFieldEntry[]): Record<string, unknown> {
  const map: Record<string, unknown> = {};
  for (const entry of entries) map[entry.field] = entry.proposedValue;
  return map;
}

function proposedValuesToStrings(value: unknown): string[] {
  if (value === null || value === undefined || value === "") return [];
  const arr = Array.isArray(value) ? value : [value];
  return arr
    .map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const obj = item as Record<string, unknown>;
        return String(obj.address ?? obj.e164 ?? obj.value ?? obj.name ?? obj.company ?? JSON.stringify(obj));
      }
      return String(item);
    })
    .map((s) => s.trim())
    .filter((s) => s.length > 0 && s !== "-" && s !== "null" && s !== "{}" && s !== "[]");
}

function PreviewRow({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) return null;
  return (
    <div className="flex gap-co-8">
      <span className="w-12 shrink-0 pt-co-2 font-mono text-10 uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 space-y-co-2 text-13 text-foreground">
        {values.map((value, index) => (
          <span key={`${label}-${index}`} className="block truncate" title={value}>
            {value}
          </span>
        ))}
      </span>
    </div>
  );
}

/**
 * ImportPreviewCard - the human-readable "what contact will this create" summary
 * for import / person.create proposals, shown instead of forcing the reviewer
 * through the master|proposed|choice field table (which is empty-master noise
 * for fresh imports). The full StructuredDiff is still available, collapsed.
 */
function ImportPreviewCard({ entries }: { entries: StructuredFieldEntry[] }) {
  const map = proposedFieldMap(entries);
  const emails = proposedValuesToStrings(map.emails ?? map.email);
  const phones = proposedValuesToStrings(map.phones ?? map.phone);
  const role = proposedValuesToStrings(map.headline ?? map.occupation_title);
  const org = proposedValuesToStrings(map.employments ?? map.organization ?? map.organizations);
  const tags = proposedValuesToStrings(map.tags);
  const notes = proposedValuesToStrings(map.notes);
  if (emails.length + phones.length + role.length + org.length + tags.length + notes.length === 0) {
    return null;
  }
  return (
    <section className="space-y-co-8 rounded-md border border-border bg-muted/25 p-co-12">
      <p className="font-mono text-11 font-medium uppercase text-muted-foreground">New contact</p>
      <div className="space-y-co-6">
        <PreviewRow label="Email" values={emails} />
        <PreviewRow label="Phone" values={phones} />
        <PreviewRow label="Role" values={role} />
        <PreviewRow label="Org" values={org} />
        <PreviewRow label="Tags" values={tags} />
        <PreviewRow label="Notes" values={notes} />
      </div>
    </section>
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
            className="inline-flex items-center gap-co-4 rounded-[var(--radius-sm)] text-12 text-link hover:underline focus-ring"
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

        <ImportPreviewCard entries={diffEntries} />

        <details className="group rounded-md border border-border bg-muted/15">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-co-4 p-co-10 font-mono text-11 font-medium uppercase text-muted-foreground hover:text-foreground">
            <span>Field-by-field details</span>
            <span className="text-muted-foreground transition-transform group-open:rotate-180">v</span>
          </summary>
          <div className="border-t border-border p-co-10">
            <StructuredDiff
              fields={diffEntries}
              choices={fieldChoices}
              perFieldChoice={tier === 3}
              onChange={onFieldChoice}
            />
          </div>
        </details>

        <EvidencePanel proposalId={proposal.proposal_id} defaultExpanded={tier === 2} />

        {proposal.case_file_links.length > 0 && (
          <section className="rounded-md border border-warning/40 bg-warning/10 p-co-12 text-12">
            <p className="mb-co-4 font-medium text-foreground">Related work</p>
            <ul className="space-y-co-4">
              {proposal.case_file_links.map((l) => (
                <li key={`${l.project_id}-${l.task_id ?? "root"}`}>
                  {env.projectsBaseUrl ? (
                    <a
                      href={`${env.projectsBaseUrl}/projects/${l.project_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-co-4 text-link hover:underline"
                    >
                      <ExternalLink className="h-3 w-3" />
                      {l.project_name}
                      {l.task_name && ` / ${l.task_name}`}
                    </a>
                  ) : (
                    <span className="inline-flex items-center gap-co-4 text-foreground">
                      {l.project_name}
                      {l.task_name && ` / ${l.task_name}`}
                    </span>
                  )}
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
