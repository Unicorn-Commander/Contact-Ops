/**
 * BulkApprovePreview — Showcase tile for the quality filter + bulk action row.
 *
 * Renders the real QualityFilterChips and BulkApproveBar with a synthetic
 * 839-proposal corpus so the orchestrator can review the surface without
 * needing the live MCP backend.
 *
 * The proposals don't actually flow through the inbox virtualizer here;
 * we just count how many would match each filter and feed that into the
 * bar's matched-count prop.
 */
import { useEffect, useMemo, useState } from "react";
import { BulkApproveBar } from "@/components/inbox/BulkApproveBar";
import {
  EMPTY_QUALITY_FILTER,
  QualityFilterChips,
  proposalMatchesQualityFilter,
  qualityFilterIsEmpty,
  type QualityFilterState
} from "@/components/inbox/QualityFilterChips";
import { applyThemePreference, type ThemeMode } from "@/design-system/tokens";
import type { Proposal } from "@/lib/types";

type Provider = "gmail" | "m365" | "icloud";

const PROVIDERS: Provider[] = ["gmail", "m365", "icloud"];
const FIRST = [
  "Avery",
  "Mina",
  "Rafael",
  "Priya",
  "Drew",
  "Sofia",
  "Jon",
  "Iris",
  "Marcus",
  "Lena"
];
const LAST = [
  "Hart",
  "Chen",
  "Ortiz",
  "Shah",
  "Kaplan",
  "Nguyen",
  "Bell",
  "Morgan",
  "Park",
  "Bauer"
];

function rand(seed: number): number {
  // Tiny deterministic PRNG so the preview is stable across renders.
  const x = Math.sin(seed) * 10000;
  return x - Math.floor(x);
}

function makeMockProposals(count: number): Proposal[] {
  const out: Proposal[] = [];
  for (let i = 0; i < count; i++) {
    const provider = PROVIDERS[i % PROVIDERS.length];
    const r = rand(i);
    const r2 = rand(i + 99);
    // Realistic connector distribution: ~75% have email (connectors lean on
    // email-keyed identity), ~55% have phone. Confidence skews high because
    // connector imports run through dedup matching that filters low-conf.
    const hasEmail = r > 0.25;
    const hasPhone = r2 > 0.45;
    const confidence = 0.75 + rand(i + 17) * 0.25;
    const first = FIRST[i % FIRST.length];
    const last = LAST[(i * 7) % LAST.length];
    const name = `${first} ${last}`;
    out.push({
      proposal_id: `pp-${i}`,
      action_event_id: `ae-${i}`,
      agent_id: `vcard-import-${provider}`,
      agent_version: "1.0",
      tenant_id: "tenant-1",
      tenant_slug: "demo",
      entity_id: `e-${i}`,
      entity_kind: "person",
      entity_display_name: name,
      action_type: "vcard_import",
      payload_before: null,
      payload_after: {
        display_name: name,
        emails: hasEmail
          ? [{ address: `${first.toLowerCase()}.${last.toLowerCase()}@example.com` }]
          : [],
        phones: hasPhone ? [{ e164: "+15555550100" }] : [],
        source: { action_subtype: provider }
      },
      confidence,
      reversibility_class: "reversible",
      compliance: { hipaa: false, exposure: "none" },
      trust_tier_at_creation: 1,
      trace_id: null,
      evidence_pack_id: null,
      parent_proposal_id: null,
      rationale: "Imported via connector",
      created_at: new Date(Date.now() - i * 60_000).toISOString(),
      snoozed_until: null,
      cross_tenant: false,
      touches_case_file: false,
      case_file_links: [],
      fields_changed: 2,
      is_dedup: false,
      is_edge: false,
      is_delete: false,
      bulk_count: 1,
      cluster_id: `c-${Math.floor(i / 4)}`
    });
  }
  return out;
}

function readUrlState(): {
  filter: QualityFilterState;
  theme: ThemeMode | null;
  scenario: "default" | "confirm" | "toast";
} {
  const params = new URLSearchParams(window.location.search);
  const themeRaw = params.get("theme");
  const theme: ThemeMode | null = themeRaw === "light" || themeRaw === "dark" ? themeRaw : null;

  const confRaw = params.get("confMin");
  let confMin: QualityFilterState["confMin"] = null;
  if (confRaw === "0.95") confMin = 0.95;
  else if (confRaw === "0.90" || confRaw === "0.9") confMin = 0.9;
  else if (confRaw === "0.80" || confRaw === "0.8") confMin = 0.8;

  const providers = (params.get("provider") ?? "")
    .split(",")
    .filter((p): p is "gmail" | "m365" | "icloud" =>
      p === "gmail" || p === "m365" || p === "icloud"
    );

  const scenarioRaw = params.get("scenario");
  const scenario: "default" | "confirm" | "toast" =
    scenarioRaw === "confirm" || scenarioRaw === "toast" ? scenarioRaw : "default";

  return {
    theme,
    scenario,
    filter: {
      confMin,
      hasEmail: params.get("hasEmail") === "1",
      hasPhone: params.get("hasPhone") === "1",
      providers
    }
  };
}

export function BulkApprovePreview() {
  const initial = useMemo(() => readUrlState(), []);
  const [filter, setFilter] = useState<QualityFilterState>(
    initial.filter ?? EMPTY_QUALITY_FILTER
  );
  const proposals = useMemo(() => makeMockProposals(839), []);

  useEffect(() => {
    if (initial.theme) applyThemePreference(initial.theme);
  }, [initial.theme]);

  const matched = useMemo(
    () =>
      qualityFilterIsEmpty(filter)
        ? proposals
        : proposals.filter((p) => proposalMatchesQualityFilter(p, filter)),
    [filter, proposals]
  );

  return (
    <div className="space-y-4 rounded-[var(--radius-lg)] border border-border bg-card p-4 shadow-[var(--shadow-1)]">
      <header className="space-y-1">
        <h2 className="text-base font-semibold">Bulk approve preview</h2>
        <p className="text-xs text-muted-foreground">
          Synthetic corpus of <span className="font-mono">{proposals.length}</span> connector-
          pulled proposals (Gmail / M365 / iCloud). Toggle chips to see live match counts.
        </p>
      </header>

      <div className="overflow-hidden rounded-md border border-border bg-background">
        <QualityFilterChips
          value={filter}
          matchedCount={matched.length}
          totalCount={proposals.length}
          onChange={setFilter}
        />
        <BulkApproveBar
          visible={!qualityFilterIsEmpty(filter)}
          matchedCount={matched.length}
          busy={initial.scenario === "confirm"}
          progress={
            initial.scenario === "confirm"
              ? { settled: Math.floor(matched.length / 3), total: matched.length, failed: 0 }
              : null
          }
          onApproveAll={() => undefined}
          onRejectAll={() => undefined}
        />
      </div>

      {initial.scenario === "confirm" ? (
        <ConfirmModalMock count={matched.length} />
      ) : null}

      {initial.scenario === "toast" ? <ToastMock count={matched.length} /> : null}

      <div className="rounded-md border border-dashed border-border bg-muted/30 p-3 text-xs text-muted-foreground">
        Picking <code className="font-mono">≥ 0.95 + Has email + From Gmail</code> on a 839-row
        corpus selects {" "}
        <span className="font-mono text-foreground">
          {
            proposals.filter((p) =>
              proposalMatchesQualityFilter(p, {
                confMin: 0.95,
                hasEmail: true,
                hasPhone: false,
                providers: ["gmail"]
              })
            ).length
          }
        </span>{" "}
        proposals — drain them in one click instead of one-by-one.
      </div>
    </div>
  );
}

// ---- Modal + toast captures (rendered inline so screenshots are stable) ----
//
// We don't actually open the Radix Dialog here because headless Chrome won't
// hold a portal open across navigations; instead we render a visually-faithful
// inline mock with the same shape.

function ConfirmModalMock({ count }: { count: number }) {
  const settled = Math.floor(count / 3);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
      <div className="w-[calc(100vw-2rem)] max-w-lg rounded-lg border border-border bg-card p-5 shadow-lg">
        <div className="mb-4 flex flex-col gap-1">
          <h3 className="flex items-center gap-2 text-base font-semibold">
            <span className="text-success">✓</span>
            Approve {count} proposals?
          </h3>
          <p className="text-sm text-muted-foreground">
            This will fire <span className="font-medium text-foreground">{count}</span> approve
            calls in parallel. Failures don&apos;t block the rest of the batch — the count of
            failed rows is reported in the success toast and on Recent activity.
          </p>
        </div>
        <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
          <p className="flex items-center gap-2">
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-muted border-t-foreground" />
            Settled count:{" "}
            <span className="co-mono-numeric font-medium text-foreground">{settled}</span>
            <span className="text-muted-foreground">/{count}</span>
          </p>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button className="rounded-md px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted">
            Cancel
          </button>
          <button className="rounded-md bg-gradient-brand px-2.5 py-1.5 text-xs font-medium text-primary-foreground">
            Working…
          </button>
        </div>
      </div>
    </div>
  );
}

function ToastMock({ count }: { count: number }) {
  const failed = 3;
  const approved = count - failed;
  return (
    <div className="fixed bottom-6 right-6 z-50 max-w-md rounded-md border border-border bg-card p-3 shadow-lg">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 text-success">✓</span>
        <div className="space-y-0.5">
          <p className="text-sm font-medium">Approved {approved} proposals</p>
          <p className="text-xs text-muted-foreground">
            ({failed} failed — see Recent activity).
          </p>
        </div>
      </div>
    </div>
  );
}
