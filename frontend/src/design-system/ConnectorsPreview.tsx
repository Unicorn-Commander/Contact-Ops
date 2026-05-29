/**
 * ConnectorsPreview — auth-free showcase of the /connectors page.
 *
 * Lives at /design-system/connectors so the page is screenshottable without
 * Keycloak or a running backend. Variants cover the canonical states:
 *
 *   - "empty"            no connectors configured (empty-state callout + grid)
 *   - "icloud-one"       single iCloud connected, idle
 *   - "icloud-pulling"   single iCloud connected, pulling now
 *   - "mixed"            three connectors with success/warn/error mix
 *   - "icloud-modal"     iCloud connect modal open
 *   - "runs-populated"   runs table with 10 rows
 *   - "mobile"           375px-wide mobile frame
 *
 * Everything is presentational. The shapes mirror `useConnectors.ts` /
 * `types.ts` field-for-field so drift between preview and real page is
 * caught at the type level.
 */
import { useMemo, useState } from "react";
import {
  AlertCircle,
  Bell,
  BookUser,
  Cable,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cloud,
  ExternalLink,
  Eye,
  History,
  Loader2,
  Mail,
  Menu,
  Plug,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X
} from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  Connector,
  ConnectorProvider,
  ConnectorRun,
  ConnectorStatus
} from "@/lib/types";
import type { ThemeMode } from "@/design-system/tokens";

// ---- Mock connectors -------------------------------------------------------

const ISO_NOW = new Date().toISOString();
const ISO_2H_AGO = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
const ISO_6H_AGO = new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString();
const ISO_1D_AGO = new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString();
const ISO_3D_AGO = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();

const MOCK_ICLOUD_CONNECTED: Connector = {
  id: "icloud-conn-id",
  provider: "icloud",
  display_name: "iCloud",
  status: "connected",
  last_pull_at: ISO_2H_AGO,
  last_pull_summary: {
    parsed_count: 312,
    proposed_count: 47,
    deduped_count: 11,
    skipped_count: 2
  },
  last_error: null,
  configured_at: ISO_3D_AGO
};

const MOCK_M365_CONNECTED: Connector = {
  id: "m365-conn-id",
  provider: "m365",
  display_name: "Microsoft 365",
  status: "connected",
  last_pull_at: ISO_6H_AGO,
  last_pull_summary: {
    parsed_count: 824,
    proposed_count: 134,
    deduped_count: 56,
    skipped_count: 8
  },
  last_error: null,
  configured_at: ISO_3D_AGO
};

const MOCK_M365_CONFIGURED: Connector = {
  id: "m365-conn-warn-id",
  provider: "m365",
  display_name: "Microsoft 365",
  status: "configured",
  last_pull_at: null,
  last_pull_summary: null,
  last_error: null,
  configured_at: ISO_NOW
};

const MOCK_GMAIL_ERROR: Connector = {
  id: "gmail-conn-id",
  provider: "gmail",
  display_name: "Gmail",
  status: "error",
  last_pull_at: ISO_1D_AGO,
  last_pull_summary: null,
  last_error:
    "OAuth refresh failed (invalid_grant). The Google account revoked our token — Reconnect to grant access again.",
  configured_at: ISO_3D_AGO
};

// ---- Mock runs -------------------------------------------------------------

const MOCK_RUNS: ConnectorRun[] = [
  {
    run_id: "run-1",
    connector_id: "icloud-conn-id",
    provider: "icloud",
    display_name: "iCloud",
    status: "succeeded",
    started_at: ISO_2H_AGO,
    finished_at: new Date(Date.now() - 2 * 60 * 60 * 1000 + 24_000).toISOString(),
    parsed_count: 312,
    proposed_count: 47,
    deduped_count: 11,
    skipped_count: 2,
    summary: { duration_ms: 24_000, source: "carddav", remote_url: "https://contacts.icloud.com" }
  },
  {
    run_id: "run-2",
    connector_id: "m365-conn-id",
    provider: "m365",
    display_name: "Microsoft 365",
    status: "succeeded",
    started_at: ISO_6H_AGO,
    finished_at: new Date(Date.now() - 6 * 60 * 60 * 1000 + 18_000).toISOString(),
    parsed_count: 824,
    proposed_count: 134,
    deduped_count: 56,
    skipped_count: 8,
    summary: { duration_ms: 18_000, source: "graph", pages_fetched: 12 }
  },
  {
    run_id: "run-3",
    connector_id: "gmail-conn-id",
    provider: "gmail",
    display_name: "Gmail",
    status: "failed",
    started_at: ISO_1D_AGO,
    finished_at: new Date(Date.now() - 26 * 60 * 60 * 1000 + 4_000).toISOString(),
    parsed_count: 0,
    proposed_count: 0,
    summary: { duration_ms: 4_000, source: "people_api" },
    error: "OAuth refresh failed: invalid_grant — Reconnect to grant access again."
  },
  {
    run_id: "run-4",
    connector_id: "icloud-conn-id",
    provider: "icloud",
    display_name: "iCloud",
    status: "partial",
    started_at: ISO_1D_AGO,
    finished_at: new Date(Date.now() - 26 * 60 * 60 * 1000 + 21_000).toISOString(),
    parsed_count: 305,
    proposed_count: 41,
    deduped_count: 9,
    skipped_count: 4,
    summary: {
      duration_ms: 21_000,
      partial_reason: "carddav_etag_drift",
      skipped_uids: ["1a2b3c", "4d5e6f"]
    }
  },
  {
    run_id: "run-5",
    connector_id: "m365-conn-id",
    provider: "m365",
    display_name: "Microsoft 365",
    status: "succeeded",
    started_at: ISO_3D_AGO,
    finished_at: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000 + 16_000).toISOString(),
    parsed_count: 813,
    proposed_count: 122,
    deduped_count: 51,
    skipped_count: 6,
    summary: { duration_ms: 16_000, source: "graph", pages_fetched: 11 }
  }
];

const MOCK_RUNS_WITH_INFLIGHT: ConnectorRun[] = [
  {
    run_id: "run-inflight",
    connector_id: "icloud-conn-id",
    provider: "icloud",
    display_name: "iCloud",
    status: "running",
    started_at: new Date(Date.now() - 8_000).toISOString(),
    summary: { phase: "fetching", batches_done: 2 }
  },
  ...MOCK_RUNS.slice(0, 4)
];

// ---- Status/tone tables (kept in sync with the real components) -----------

interface ToneSpec {
  label: string;
  tone: "emerald" | "sky" | "rose" | "amber" | "muted";
}

const STATUS_TONE: Record<ConnectorStatus | "not_configured", ToneSpec> = {
  not_configured: { label: "Not configured", tone: "muted" },
  configured: { label: "Configured", tone: "sky" },
  connected: { label: "Connected", tone: "emerald" },
  disconnected: { label: "Disconnected", tone: "muted" },
  error: { label: "Error", tone: "rose" }
};

function StatusBadge({ status }: { status: keyof typeof STATUS_TONE }) {
  const spec = STATUS_TONE[status];
  const dotClass: Record<ToneSpec["tone"], string> = {
    emerald: "bg-[oklch(var(--co-emerald-500))]",
    sky: "bg-[oklch(var(--co-sky-500))]",
    rose: "bg-[oklch(var(--co-rose-500))]",
    amber: "bg-[oklch(var(--co-amber-500))]",
    muted: "bg-muted-foreground/70"
  };
  const chipClass: Record<ToneSpec["tone"], string> = {
    emerald:
      "bg-[oklch(var(--co-emerald-500)/0.14)] text-[oklch(var(--co-emerald-500))] border-[oklch(var(--co-emerald-500)/0.32)]",
    sky:
      "bg-[oklch(var(--co-sky-500)/0.14)] text-[oklch(var(--co-sky-500))] border-[oklch(var(--co-sky-500)/0.32)]",
    rose:
      "bg-[oklch(var(--co-rose-500)/0.14)] text-[oklch(var(--co-rose-500))] border-[oklch(var(--co-rose-500)/0.32)]",
    amber:
      "bg-[oklch(var(--co-amber-500)/0.14)] text-[oklch(var(--co-amber-500))] border-[oklch(var(--co-amber-500)/0.32)]",
    muted: "bg-muted text-muted-foreground border-border"
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        chipClass[spec.tone]
      )}
    >
      <span className={cn("inline-block h-1.5 w-1.5 rounded-full", dotClass[spec.tone])} aria-hidden="true" />
      {spec.label}
    </span>
  );
}

// ---- Provider card (presentational twin of ProviderCard) ------------------

interface ProviderDef {
  provider: ConnectorProvider;
  name: string;
  description: string;
  icon: typeof Cloud;
  iconColor: string;
}

const PROVIDERS: ProviderDef[] = [
  {
    provider: "icloud",
    name: "iCloud",
    description: "Pull contacts via CardDAV using an app-specific password.",
    icon: Cloud,
    iconColor: "text-[oklch(var(--co-sky-500))]"
  },
  {
    provider: "m365",
    name: "Microsoft 365",
    description: "Pull contacts via Microsoft Graph; sign in with Microsoft.",
    icon: Mail,
    iconColor: "text-[oklch(var(--co-amber-500))]"
  },
  {
    provider: "gmail",
    name: "Gmail",
    description: "Pull contacts via Google People; sign in with Google.",
    icon: Mail,
    iconColor: "text-[oklch(var(--co-rose-500))]"
  }
];

function formatRelativeTime(iso?: string | null): string {
  if (!iso) return "Never";
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return "Never";
  const diff = Date.now() - ts;
  if (diff < 0) return "Just now";
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} ${days === 1 ? "day" : "days"} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} ${months === 1 ? "month" : "months"} ago`;
  const years = Math.floor(months / 12);
  return `${years} ${years === 1 ? "year" : "years"} ago`;
}

function formatRelativeShort(iso: string): string {
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return iso;
  const diff = Date.now() - ts;
  if (diff < 0) return "Just now";
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

// Auth-shape hint + per-card action hint mirror PROVIDER_AUTH_HINT /
// PROVIDER_ACTION_HINT in ProviderCard.tsx so the preview matches the
// real component byte-for-byte.
const AUTH_HINT: Record<ConnectorProvider, string> = {
  icloud: "Apple ID + an app-specific password",
  m365: "Sign in with Microsoft (read-only contacts)",
  gmail: "Sign in with Google (read-only contacts)"
};
const ACTION_HINT: Partial<Record<ConnectorProvider, string>> = {
  m365:
    "Opens Microsoft sign-in in a popup. You'll grant Contact-Ops read-only access to your contacts.",
  gmail:
    "Opens Google sign-in in a popup. You'll grant Contact-Ops read-only access to your contacts."
};

function MockProviderCard({
  def,
  connector,
  status,
  pulling
}: {
  def: ProviderDef;
  connector?: Connector;
  status: keyof typeof STATUS_TONE;
  pulling?: boolean;
}) {
  const Icon = def.icon;
  const railClass: Record<keyof typeof STATUS_TONE, string> = {
    not_configured: "bg-muted-foreground/30",
    configured: "bg-[oklch(var(--co-sky-500))]",
    connected: "bg-[oklch(var(--co-emerald-500))]",
    disconnected: "bg-muted-foreground/40",
    error: "bg-[oklch(var(--co-rose-500))]"
  };

  const showActions = (() => {
    if (status === "not_configured" || status === "disconnected") {
      return (
        <>
          <span className="bg-primary text-primary-foreground inline-flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium">
            <Plug className="h-4 w-4" strokeWidth={1.8} />
            {status === "not_configured" ? "Connect" : "Reconnect"}
          </span>
        </>
      );
    }
    if (status === "error") {
      return (
        <>
          <span className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-medium">
            <Trash2 className="h-4 w-4" strokeWidth={1.8} />
            Disconnect
          </span>
          <span className="bg-primary text-primary-foreground inline-flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium">
            <RefreshCw className="h-4 w-4" strokeWidth={1.8} />
            Reconnect
          </span>
        </>
      );
    }
    return (
      <>
        <span className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-muted-foreground">
          <Trash2 className="h-3.5 w-3.5" strokeWidth={1.8} />
          Disconnect
        </span>
        <span className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-medium">
          <ChevronDown className="h-4 w-4" strokeWidth={1.8} />
          Options
        </span>
        <span
          className={cn(
            "bg-gradient-brand inline-flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium text-primary-foreground shadow-[var(--shadow-1)]",
            pulling && "opacity-90"
          )}
        >
          {pulling ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.8} />
              Pulling…
            </>
          ) : (
            <>
              <RefreshCw className="h-4 w-4" strokeWidth={1.8} />
              Pull now
            </>
          )}
        </span>
      </>
    );
  })();

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border bg-card text-card-foreground shadow-[var(--shadow-1)]",
        status === "error" ? "border-[oklch(var(--co-rose-500)/0.45)]" : "border-border"
      )}
    >
      <span
        aria-hidden="true"
        className={cn("absolute left-0 top-0 h-full w-[3px]", railClass[status])}
      />
      <div className="flex flex-col gap-4 p-5 pl-6">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span
              className={cn(
                "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl",
                status === "connected"
                  ? "bg-gradient-brand text-primary-foreground shadow-[var(--shadow-1)]"
                  : status === "configured"
                    ? "bg-[oklch(var(--co-fuchsia-500)/0.18)] text-[oklch(var(--co-fuchsia-500))]"
                    : "bg-muted text-foreground"
              )}
            >
              <Icon className={cn("h-5 w-5", def.iconColor)} strokeWidth={1.8} />
            </span>
            <div className="min-w-0">
              <h3 className="text-base font-semibold leading-tight tracking-tight">{def.name}</h3>
              {status === "not_configured" ? (
                <p className="mt-0.5 text-[11px] font-medium text-foreground/80">
                  {AUTH_HINT[def.provider]}
                </p>
              ) : null}
              <p className="mt-0.5 text-xs text-muted-foreground">{def.description}</p>
            </div>
          </div>
          <StatusBadge status={status} />
        </div>

        <div className="space-y-2">
          {status === "error" && connector?.last_error ? (
            <div className="flex items-start gap-2 rounded-md border border-[oklch(var(--co-rose-500)/0.35)] bg-[oklch(var(--co-rose-500)/0.06)] px-3 py-2 text-xs leading-snug text-foreground">
              <AlertCircle
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[oklch(var(--co-rose-500))]"
                strokeWidth={2}
              />
              <span>{connector.last_error}</span>
            </div>
          ) : null}

          {(status === "connected" || status === "configured") && !connector?.last_error ? (
            pulling ? (
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="inline-flex h-1.5 w-1.5 rounded-full bg-[oklch(var(--co-sky-500))] co-animate-pulse-subtle" />
                Pulling now…
              </p>
            ) : connector?.last_pull_at ? (
              <p className="text-xs text-muted-foreground">
                Last pull:{" "}
                <span className="text-foreground/85">{formatRelativeTime(connector.last_pull_at)}</span>
                {typeof connector.last_pull_summary?.proposed_count === "number" ? (
                  <>
                    {" "}
                    ·{" "}
                    <span className="font-medium text-foreground">
                      {connector.last_pull_summary.proposed_count}
                    </span>{" "}
                    proposed
                  </>
                ) : null}
              </p>
            ) : (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Plug className="h-3.5 w-3.5" strokeWidth={1.8} />
                Ready for first pull
              </p>
            )
          ) : null}

          {status === "not_configured" ? (
            <div className="space-y-1.5">
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Plug className="h-3.5 w-3.5" strokeWidth={1.8} />
                Not connected yet
              </p>
              {ACTION_HINT[def.provider] ? (
                <p className="text-[11px] leading-snug text-muted-foreground">
                  {ACTION_HINT[def.provider]}
                </p>
              ) : null}
              <p className="flex items-center gap-1 text-[11px]">
                <span className="inline-block h-3 w-3 rounded-full border border-muted-foreground/40" aria-hidden="true" />
                <span className="text-link underline-offset-2">Learn more</span>
              </p>
            </div>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2">{showActions}</div>
      </div>
    </div>
  );
}

// ---- Runs table -----------------------------------------------------------

const PROVIDER_CHIPS: Record<ConnectorProvider, { label: string; icon: typeof Cloud; iconColor: string }> = {
  icloud: { label: "iCloud", icon: Cloud, iconColor: "text-[oklch(var(--co-sky-500))]" },
  m365: { label: "Microsoft 365", icon: Mail, iconColor: "text-[oklch(var(--co-amber-500))]" },
  gmail: { label: "Gmail", icon: Mail, iconColor: "text-[oklch(var(--co-rose-500))]" }
};

const RUN_PILLS: Record<
  ConnectorRun["status"],
  { label: string; classes: string }
> = {
  running: {
    label: "Running",
    classes:
      "bg-[oklch(var(--co-sky-500)/0.14)] text-[oklch(var(--co-sky-500))] border-[oklch(var(--co-sky-500)/0.3)]"
  },
  succeeded: {
    label: "Succeeded",
    classes:
      "bg-[oklch(var(--co-emerald-500)/0.14)] text-[oklch(var(--co-emerald-500))] border-[oklch(var(--co-emerald-500)/0.3)]"
  },
  failed: {
    label: "Failed",
    classes:
      "bg-[oklch(var(--co-rose-500)/0.14)] text-[oklch(var(--co-rose-500))] border-[oklch(var(--co-rose-500)/0.3)]"
  },
  partial: {
    label: "Partial",
    classes:
      "bg-[oklch(var(--co-amber-500)/0.16)] text-[oklch(var(--co-amber-500))] border-[oklch(var(--co-amber-500)/0.3)]"
  }
};

function MockRunsTable({ runs, expandedFirst }: { runs: ConnectorRun[]; expandedFirst?: boolean }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card shadow-[var(--shadow-1)]">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2">
          <History className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
          <p className="text-sm font-semibold leading-tight">Recent runs</p>
        </div>
        <p className="text-xs text-muted-foreground">Most recent {runs.length} of last 10</p>
      </div>
      {runs.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <History className="h-5 w-5" strokeWidth={1.6} />
          </span>
          <p className="text-sm font-medium">No runs yet</p>
          <p className="max-w-xs text-xs text-muted-foreground">
            Triggering a pull from any provider above will land its first run here.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full table-fixed text-left">
            <thead className="border-b border-border bg-background/40 text-[11px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="w-[28%] px-4 py-2 font-medium sm:w-[24%]">Provider</th>
                <th className="hidden w-[18%] px-4 py-2 font-medium sm:table-cell">Started</th>
                <th className="w-[24%] px-4 py-2 font-medium sm:w-[16%]">Status</th>
                <th className="hidden w-[28%] px-4 py-2 font-medium md:table-cell">Counts</th>
                <th className="w-[14%] px-2 py-2 text-right font-medium">Details</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run, idx) => {
                const chip = PROVIDER_CHIPS[run.provider];
                const Icon = chip.icon;
                const pill = RUN_PILLS[run.status];
                const expanded = expandedFirst && idx === 0;
                return (
                  <>
                    <tr key={run.run_id} className="border-b border-border last:border-b-0">
                      <td className="px-4 py-2.5">
                        <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-0.5 text-xs">
                          <Icon className={cn("h-3.5 w-3.5", chip.iconColor)} strokeWidth={1.8} />
                          <span className="truncate">{run.display_name || chip.label}</span>
                        </span>
                      </td>
                      <td className="hidden px-4 py-2.5 text-xs text-muted-foreground sm:table-cell">
                        {formatRelativeShort(run.started_at)}
                      </td>
                      <td className="px-4 py-2.5">
                        <span
                          className={cn(
                            "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium",
                            pill.classes
                          )}
                        >
                          {run.status === "running" ? (
                            <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} />
                          ) : null}
                          {pill.label}
                        </span>
                      </td>
                      <td className="hidden px-4 py-2.5 text-xs co-mono-numeric text-muted-foreground md:table-cell">
                        {typeof run.parsed_count === "number" || typeof run.proposed_count === "number"
                          ? `${run.parsed_count ?? "—"} parsed / ${run.proposed_count ?? "—"} proposed`
                          : "—"}
                      </td>
                      <td className="px-2 py-2.5 text-right">
                        <span className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground">
                          {expanded ? (
                            <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.8} />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.8} />
                          )}
                          {expanded ? "Hide" : "Details"}
                        </span>
                      </td>
                    </tr>
                    {expanded ? (
                      <tr key={`${run.run_id}-detail`} className="bg-background/40">
                        <td colSpan={5} className="px-4 py-3">
                          {run.error ? (
                            <div className="mb-2 flex items-start gap-2 rounded-md border border-[oklch(var(--co-rose-500)/0.35)] bg-[oklch(var(--co-rose-500)/0.06)] px-3 py-2 text-xs leading-snug">
                              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[oklch(var(--co-rose-500))]" strokeWidth={2} />
                              <span>{run.error}</span>
                            </div>
                          ) : null}
                          {run.summary ? (
                            <pre className="overflow-x-auto rounded-md border border-border bg-background/60 p-3 font-mono text-[11px] leading-snug text-muted-foreground">
                              {JSON.stringify(run.summary, null, 2)}
                            </pre>
                          ) : (
                            <p className="text-xs text-muted-foreground">No additional summary.</p>
                          )}
                        </td>
                      </tr>
                    ) : null}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- iCloud modal mock ----------------------------------------------------

function MockIcloudModal({ withError }: { withError?: boolean }) {
  // Scoped to the parent frame (absolute, not fixed) so the showcase grid can
  // render multiple frames stacked vertically without each modal washing the
  // whole viewport with the overlay.
  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-background/80 px-4 backdrop-blur-sm">
      <div className="relative w-[calc(100%-2rem)] max-w-md rounded-lg border border-border bg-card p-5 shadow-lg">
        <button className="absolute right-2 top-2 inline-flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted">
          <X className="h-4 w-4" />
        </button>
        <div className="mb-4 flex flex-col gap-1">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[oklch(var(--co-sky-500)/0.15)] text-[oklch(var(--co-sky-500))]">
              <Cloud className="h-5 w-5" strokeWidth={1.8} />
            </span>
            <div>
              <p className="text-base font-semibold leading-none">Connect iCloud</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Pull contacts from iCloud via CardDAV. Everything lands as proposals in your Review Queue.
              </p>
            </div>
          </div>
        </div>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground">Apple ID</label>
            <div className="flex h-9 w-full items-center rounded-md border bg-background px-3 text-sm">
              REMOVED
            </div>
          </div>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs font-medium text-foreground">
              <span>App-specific password</span>
              <span className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] font-medium text-muted-foreground">
                <Eye className="h-3 w-3" strokeWidth={1.8} />
                Show
              </span>
            </div>
            <div className="flex h-9 w-full items-center rounded-md border bg-background px-3 font-mono text-sm">
              ••••-••••-••••-••••
            </div>
            <p className="text-[11px] leading-snug text-muted-foreground">
              Generate at{" "}
              <span className="text-link underline-offset-2">
                appleid.apple.com
                <ExternalLink className="ml-0.5 inline-block h-2.5 w-2.5" strokeWidth={2} />
              </span>{" "}
              → Sign-In and Security → App-Specific Passwords.
            </p>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground">Display name</label>
            <div className="flex h-9 w-full items-center rounded-md border bg-background px-3 text-sm">
              iCloud
            </div>
            <p className="text-[11px] text-muted-foreground">Shown on the card and in the audit log.</p>
          </div>
          {withError ? (
            <div className="flex items-start gap-2 rounded-md border border-[oklch(var(--co-rose-500)/0.4)] bg-[oklch(var(--co-rose-500)/0.07)] px-3 py-2 text-xs leading-snug">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[oklch(var(--co-rose-500))]" strokeWidth={2} />
              <span>
                Apple rejected the password. Re-check the 16-character app-specific password and try again.
              </span>
            </div>
          ) : null}
          <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <span className="inline-flex h-9 items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium">
              Cancel
            </span>
            <span className="bg-gradient-brand inline-flex h-9 items-center justify-center gap-2 rounded-md px-3 text-sm font-medium text-primary-foreground shadow-[var(--shadow-1)]">
              <Cloud className="h-4 w-4" strokeWidth={1.8} />
              Connect iCloud
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---- Outer shell mock (sidebar + topbar) ---------------------------------

function MockTopBar() {
  return (
    <header className="co-glass sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border px-3">
      <span className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground lg:hidden">
        <Menu className="h-4 w-4" strokeWidth={1.8} />
      </span>
      <div className="flex items-center gap-2 rounded-md border border-border bg-background/60 px-2 py-1 text-[11px] text-muted-foreground">
        <BookUser className="h-3.5 w-3.5 text-[oklch(var(--co-brand-500))]" strokeWidth={1.8} />
        Magic Unicorn
      </div>
      <button className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 text-[11px] text-muted-foreground">
        <Search className="h-3.5 w-3.5" strokeWidth={1.8} />
        Search…
      </button>
      <span className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground">
        <Bell className="h-4 w-4" strokeWidth={1.8} />
      </span>
    </header>
  );
}

function MockSidebar({ compact }: { compact?: boolean }) {
  if (compact) return null;
  const items = [
    { label: "Dashboard", icon: History, accent: "text-[oklch(var(--co-brand-500))]" },
    { label: "People", icon: History, accent: "text-[oklch(var(--co-sky-500))]" },
    { label: "Organizations", icon: History, accent: "text-[oklch(var(--co-emerald-500))]" },
    { label: "Tags", icon: History, accent: "text-[oklch(var(--co-amber-500))]" },
    { label: "Graph", icon: History, accent: "text-[oklch(var(--co-fuchsia-500))]" },
    { label: "Review Queue", icon: History, accent: "text-[oklch(var(--co-rose-500))]" },
    { label: "Import", icon: Upload, accent: "text-[oklch(var(--co-emerald-500))]" },
    {
      label: "Connectors",
      icon: Cable,
      active: true,
      accent: "text-[oklch(var(--co-fuchsia-500))]"
    }
  ] as const;
  return (
    <aside className="hidden w-44 shrink-0 border-r border-border bg-[oklch(var(--sidebar))] lg:block">
      <div className="flex h-14 items-center gap-2 border-b border-border px-3">
        <span className="bg-gradient-brand flex h-7 w-7 items-center justify-center rounded-lg text-primary-foreground">
          <BookUser className="h-3.5 w-3.5" strokeWidth={1.8} />
        </span>
        <span className="text-xs font-semibold">Contact-Ops</span>
      </div>
      <nav className="space-y-1 p-2">
        {items.map((item) => (
          <span
            key={item.label}
            className={cn(
              "flex h-8 items-center gap-2 rounded-md border-l-2 px-2 text-[11px] font-medium",
              "active" in item && item.active
                ? "border-primary bg-primary/10 text-foreground"
                : "border-transparent text-muted-foreground"
            )}
          >
            <item.icon className={cn("h-3.5 w-3.5", item.accent)} strokeWidth={1.8} />
            {item.label}
          </span>
        ))}
      </nav>
    </aside>
  );
}

function MockPageHeader() {
  return (
    <header className="space-y-2">
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[oklch(var(--co-fuchsia-500)/0.18)] text-[oklch(var(--co-fuchsia-500))]">
          <Cable className="h-5 w-5" strokeWidth={1.8} />
        </span>
        <h1 className="text-2xl font-semibold tracking-tight">Connectors</h1>
      </div>
      <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
        Pull contacts from iCloud, Microsoft 365, or Gmail. Each pull lands records as proposals in
        your <span className="font-medium text-foreground">Review Queue</span> — nothing is applied
        without your nod.
      </p>
    </header>
  );
}

function MockEmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-card/40 px-6 py-12 text-center">
      <span className="bg-gradient-brand co-glow-brand flex h-14 w-14 items-center justify-center rounded-2xl text-primary-foreground">
        <Plug className="h-6 w-6" strokeWidth={1.8} />
      </span>
      <p className="text-base font-semibold tracking-tight">Connect a source to start pulling contacts.</p>
      <p className="mx-auto max-w-md text-sm leading-relaxed text-muted-foreground">
        Everything is proposed; nothing is applied without your approval.
      </p>
    </div>
  );
}

function MockOauthSuccessBanner({ provider }: { provider: ConnectorProvider }) {
  const name = PROVIDERS.find((p) => p.provider === provider)?.name ?? provider;
  return (
    <div className="flex items-start gap-3 rounded-xl border border-[oklch(var(--co-emerald-500)/0.4)] bg-[oklch(var(--co-emerald-500)/0.08)] px-4 py-3 text-sm">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[oklch(var(--co-emerald-500)/0.18)] text-[oklch(var(--co-emerald-500))]">
        <CheckCircle2 className="h-4 w-4" strokeWidth={2} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium leading-snug">{name} connected</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Pulling the first batch now — proposals will appear in your Review Queue shortly.
        </p>
      </div>
      <span className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground">
        <X className="h-3.5 w-3.5" strokeWidth={1.8} />
      </span>
    </div>
  );
}

// ---- Variants -------------------------------------------------------------

type Variant =
  | "empty"
  | "icloud-one"
  | "icloud-pulling"
  | "mixed"
  | "icloud-modal"
  | "icloud-modal-error"
  | "runs-populated"
  | "oauth-success";

function VariantBody({ variant }: { variant: Variant }) {
  if (variant === "empty") {
    return (
      <>
        <MockPageHeader />
        <MockEmptyState />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {PROVIDERS.map((def) => (
            <MockProviderCard key={def.provider} def={def} status="not_configured" />
          ))}
        </div>
        <MockRunsTable runs={[]} />
      </>
    );
  }
  if (variant === "icloud-one") {
    return (
      <>
        <MockPageHeader />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <MockProviderCard
            def={PROVIDERS[0]}
            status="connected"
            connector={MOCK_ICLOUD_CONNECTED}
          />
          <MockProviderCard def={PROVIDERS[1]} status="not_configured" />
          <MockProviderCard def={PROVIDERS[2]} status="not_configured" />
        </div>
        <MockRunsTable runs={MOCK_RUNS.slice(0, 1)} />
      </>
    );
  }
  if (variant === "icloud-pulling") {
    return (
      <>
        <MockPageHeader />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <MockProviderCard
            def={PROVIDERS[0]}
            status="connected"
            connector={MOCK_ICLOUD_CONNECTED}
            pulling
          />
          <MockProviderCard def={PROVIDERS[1]} status="not_configured" />
          <MockProviderCard def={PROVIDERS[2]} status="not_configured" />
        </div>
        <MockRunsTable runs={MOCK_RUNS_WITH_INFLIGHT} />
      </>
    );
  }
  if (variant === "mixed") {
    return (
      <>
        <MockPageHeader />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <MockProviderCard
            def={PROVIDERS[0]}
            status="connected"
            connector={MOCK_ICLOUD_CONNECTED}
          />
          <MockProviderCard
            def={PROVIDERS[1]}
            status="configured"
            connector={MOCK_M365_CONFIGURED}
          />
          <MockProviderCard def={PROVIDERS[2]} status="error" connector={MOCK_GMAIL_ERROR} />
        </div>
        <MockRunsTable runs={MOCK_RUNS} expandedFirst />
      </>
    );
  }
  if (variant === "icloud-modal") {
    return (
      <>
        <MockPageHeader />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {PROVIDERS.map((def) => (
            <MockProviderCard key={def.provider} def={def} status="not_configured" />
          ))}
        </div>
        <MockRunsTable runs={[]} />
        <MockIcloudModal />
      </>
    );
  }
  if (variant === "icloud-modal-error") {
    return (
      <>
        <MockPageHeader />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {PROVIDERS.map((def) => (
            <MockProviderCard key={def.provider} def={def} status="not_configured" />
          ))}
        </div>
        <MockRunsTable runs={[]} />
        <MockIcloudModal withError />
      </>
    );
  }
  if (variant === "runs-populated") {
    return (
      <>
        <MockPageHeader />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <MockProviderCard
            def={PROVIDERS[0]}
            status="connected"
            connector={MOCK_ICLOUD_CONNECTED}
          />
          <MockProviderCard
            def={PROVIDERS[1]}
            status="connected"
            connector={MOCK_M365_CONNECTED}
          />
          <MockProviderCard def={PROVIDERS[2]} status="error" connector={MOCK_GMAIL_ERROR} />
        </div>
        <MockRunsTable runs={MOCK_RUNS} expandedFirst />
      </>
    );
  }
  if (variant === "oauth-success") {
    return (
      <>
        <MockPageHeader />
        <MockOauthSuccessBanner provider="m365" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <MockProviderCard def={PROVIDERS[0]} status="not_configured" />
          <MockProviderCard
            def={PROVIDERS[1]}
            status="connected"
            connector={MOCK_M365_CONNECTED}
            pulling
          />
          <MockProviderCard def={PROVIDERS[2]} status="not_configured" />
        </div>
        <MockRunsTable runs={MOCK_RUNS_WITH_INFLIGHT} />
      </>
    );
  }
  return null;
}

// ---- Frame ---------------------------------------------------------------

function Frame({
  theme,
  variant,
  width = "100%",
  compact = false
}: {
  theme: ThemeMode;
  variant: Variant;
  width?: string | number;
  compact?: boolean;
}) {
  return (
    <div
      data-theme={theme}
      style={{ width }}
      className={cn(
        "relative isolate overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background text-foreground shadow-[var(--shadow-2)]"
      )}
    >
      <div className="flex">
        <MockSidebar compact={compact} />
        <div className="min-w-0 flex-1">
          <MockTopBar />
          <div className={cn("mx-auto max-w-5xl space-y-6 p-4 lg:p-6", compact && "p-3")}>
            <VariantBody variant={variant} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ---- Public component ----------------------------------------------------

export function ConnectorsPreview() {
  // Each preview is keyed by variant + theme so the screenshot harness can
  // address them deterministically.
  const previews = useMemo(
    () => [
      { theme: "dark" as ThemeMode, variant: "empty" as Variant, title: "Empty (dark)" },
      { theme: "light" as ThemeMode, variant: "empty" as Variant, title: "Empty (light)" },
      {
        theme: "dark" as ThemeMode,
        variant: "icloud-one" as Variant,
        title: "iCloud configured + idle (dark)"
      },
      {
        theme: "light" as ThemeMode,
        variant: "icloud-one" as Variant,
        title: "iCloud configured + idle (light)"
      },
      {
        theme: "dark" as ThemeMode,
        variant: "icloud-pulling" as Variant,
        title: "iCloud pulling now (dark)"
      },
      {
        theme: "dark" as ThemeMode,
        variant: "mixed" as Variant,
        title: "Three connectors mixed status (dark)"
      },
      {
        theme: "light" as ThemeMode,
        variant: "mixed" as Variant,
        title: "Three connectors mixed status (light)"
      },
      {
        theme: "dark" as ThemeMode,
        variant: "icloud-modal" as Variant,
        title: "iCloud connect modal (dark)"
      },
      {
        theme: "dark" as ThemeMode,
        variant: "icloud-modal-error" as Variant,
        title: "iCloud modal with invalid-credentials (dark)"
      },
      {
        theme: "dark" as ThemeMode,
        variant: "runs-populated" as Variant,
        title: "Recent runs populated (dark)"
      },
      {
        theme: "dark" as ThemeMode,
        variant: "oauth-success" as Variant,
        title: "OAuth success banner (dark)"
      }
    ],
    []
  );

  // Tab-style switcher for the screenshot harness; default = empty.
  const [activeIdx, setActiveIdx] = useState(0);
  const active = previews[activeIdx];

  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-base font-semibold">Connectors</h2>
        <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
          The /connectors page — pull contacts from iCloud (CardDAV), Microsoft 365 (Graph), or Gmail
          (People). Every record lands as a proposal in the Review Queue. Eleven variants across both
          themes plus a 375px mobile frame at the bottom.
        </p>
      </div>

      {/* Variant selector — useful while iterating in the showcase. */}
      <div className="flex flex-wrap gap-1.5">
        {previews.map((preview, idx) => (
          <button
            key={`${preview.theme}-${preview.variant}-${idx}`}
            type="button"
            onClick={() => setActiveIdx(idx)}
            className={cn(
              "rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
              activeIdx === idx
                ? "border-primary bg-primary/10 text-foreground"
                : "border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            {preview.title}
          </button>
        ))}
      </div>

      {/* Active variant — large frame */}
      <div className="space-y-2">
        <h3 className="text-sm font-medium">{active.title}</h3>
        <Frame theme={active.theme} variant={active.variant} />
      </div>

      {/* Grid of all variants for the per-frame screenshot pass */}
      <div className="grid gap-6 2xl:grid-cols-2">
        {previews.map((preview) => (
          <div key={`grid-${preview.theme}-${preview.variant}`} className="space-y-2">
            <h3 className="text-sm font-medium">{preview.title}</h3>
            <Frame theme={preview.theme} variant={preview.variant} />
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Mobile (375px) · mixed (dark)</h3>
        <div className="mx-auto w-[375px] max-w-full">
          <Frame theme="dark" variant="mixed" width={375} compact />
        </div>
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Mobile (375px) · empty (light)</h3>
        <div className="mx-auto w-[375px] max-w-full">
          <Frame theme="light" variant="empty" width={375} compact />
        </div>
      </div>
    </section>
  );
}

export default ConnectorsPreview;
