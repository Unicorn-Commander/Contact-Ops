/**
 * RunsTable — recent connector_runs across all configured providers.
 *
 * Mounts under the provider-card grid. Each row is collapsible; expanding the
 * row pretty-prints the run's `summary` jsonb (and `error` when set).
 *
 * Columns:
 *   Provider chip · Started (relative) · Status pill · Parsed / Proposed · Expand
 *
 * Empty state: friendly "No runs yet" hint with a small icon, no panic.
 * Skeleton state: three placeholder rows while the first fetch is in flight.
 *
 * The table is presentational — the parent owns the polling cadence.
 */
import { useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Cloud,
  History,
  Loader2,
  Mail
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type {
  ConnectorProvider,
  ConnectorRun,
  ConnectorRunStatus
} from "@/lib/types";

// ---- Provider chip --------------------------------------------------------

interface ProviderChipSpec {
  label: string;
  icon: typeof Cloud;
  iconColor: string;
}

const PROVIDER_CHIPS: Record<ConnectorProvider, ProviderChipSpec> = {
  icloud: {
    label: "iCloud",
    icon: Cloud,
    iconColor: "text-[oklch(var(--co-sky-500))]"
  },
  m365: {
    label: "Microsoft 365",
    icon: Mail,
    iconColor: "text-[oklch(var(--co-amber-500))]"
  },
  gmail: {
    label: "Gmail",
    icon: Mail,
    iconColor: "text-[oklch(var(--co-rose-500))]"
  }
};

function ProviderChip({
  provider,
  name
}: {
  // Defensive: backend JOIN can return NULL on a disconnected connector
  // (LEFT JOIN miss) and historical bundles returned no provider field
  // at all — render an "unknown" chip instead of crashing.
  provider: ConnectorProvider | null | undefined;
  name?: string | null;
}) {
  const spec = provider ? PROVIDER_CHIPS[provider] : undefined;
  if (!spec) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-0.5 text-xs text-muted-foreground">
        <span className="truncate" title={name ?? "Unknown source"}>
          {name ?? "Unknown source"}
        </span>
      </span>
    );
  }
  const Icon = spec.icon;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-0.5 text-xs">
      <Icon className={cn("h-3.5 w-3.5", spec.iconColor)} strokeWidth={1.8} aria-hidden="true" />
      <span className="truncate" title={name || spec.label}>
        {name || spec.label}
      </span>
    </span>
  );
}

// ---- Status pill ----------------------------------------------------------

interface StatusPillSpec {
  label: string;
  classes: string;
}

const STATUS_PILLS: Record<ConnectorRunStatus, StatusPillSpec> = {
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

function StatusPill({ status }: { status: ConnectorRunStatus }) {
  const spec = STATUS_PILLS[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        spec.classes
      )}
    >
      {status === "running" ? (
        <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} aria-hidden="true" />
      ) : null}
      {spec.label}
    </span>
  );
}

// ---- Relative time --------------------------------------------------------

function formatRelative(iso: string): string {
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

// ---- Row ------------------------------------------------------------------

function RunRow({ run }: { run: ConnectorRun }) {
  const [open, setOpen] = useState(false);
  const hasDetails =
    (run.summary && Object.keys(run.summary).length > 0) || Boolean(run.error);
  const parsed = run.parsed_count;
  const proposed = run.proposed_count;
  const counts =
    typeof parsed === "number" || typeof proposed === "number"
      ? `${typeof parsed === "number" ? parsed.toLocaleString() : "—"} parsed / ${
          typeof proposed === "number" ? proposed.toLocaleString() : "—"
        } proposed`
      : "—";

  return (
    <>
      <tr
        className={cn(
          "border-b border-border last:border-b-0 transition-colors",
          hasDetails ? "hover:bg-muted/40" : "hover:bg-muted/20"
        )}
      >
        <td className="px-4 py-2.5">
          <ProviderChip provider={run.provider} name={run.display_name} />
        </td>
        <td className="hidden px-4 py-2.5 text-xs text-muted-foreground sm:table-cell">
          <span title={new Date(run.started_at).toLocaleString()}>
            {formatRelative(run.started_at)}
          </span>
        </td>
        <td className="px-4 py-2.5">
          <StatusPill status={run.status} />
        </td>
        <td className="hidden px-4 py-2.5 text-xs co-mono-numeric text-muted-foreground md:table-cell">
          {counts}
        </td>
        <td className="px-2 py-2.5 text-right">
          {hasDetails ? (
            <button
              type="button"
              onClick={() => setOpen((prev) => !prev)}
              className="focus-ring inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-expanded={open}
              aria-label={open ? "Hide run details" : "Show run details"}
            >
              {open ? (
                <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden="true" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden="true" />
              )}
              {open ? "Hide" : "Details"}
            </button>
          ) : (
            <span className="text-[11px] text-muted-foreground/60">—</span>
          )}
        </td>
      </tr>
      {open ? (
        <tr className="bg-background/40">
          <td colSpan={5} className="px-4 py-3">
            {run.error ? (
              <div className="mb-2 flex items-start gap-2 rounded-md border border-[oklch(var(--co-rose-500)/0.35)] bg-[oklch(var(--co-rose-500)/0.06)] px-3 py-2 text-xs leading-snug">
                <AlertCircle
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[oklch(var(--co-rose-500))]"
                  strokeWidth={2}
                  aria-hidden="true"
                />
                <span>{run.error}</span>
              </div>
            ) : null}
            {run.summary && Object.keys(run.summary).length > 0 ? (
              <pre className="co-scrollbar overflow-x-auto rounded-md border border-border bg-background/60 p-3 font-mono text-[11px] leading-snug text-muted-foreground">
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
}

// ---- Public component -----------------------------------------------------

export interface RunsTableProps {
  runs: ConnectorRun[];
  isLoading?: boolean;
}

export function RunsTable({ runs, isLoading }: RunsTableProps) {
  const empty = !isLoading && runs.length === 0;

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card shadow-[var(--shadow-1)]">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2">
          <History className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} aria-hidden="true" />
          <p className="text-sm font-semibold leading-tight">Recent runs</p>
        </div>
        <p className="text-xs text-muted-foreground">Most recent {runs.length || 0} of last 10</p>
      </div>

      {empty ? (
        <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
          <span
            className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground"
            aria-hidden="true"
          >
            <History className="h-5 w-5" strokeWidth={1.6} />
          </span>
          <p className="text-sm font-medium">No runs yet</p>
          <p className="max-w-xs text-xs text-muted-foreground">
            Triggering a pull from any provider above will land its first run here.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto co-scrollbar">
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
              {isLoading
                ? Array.from({ length: 3 }).map((_, idx) => (
                    <tr key={idx} className="border-b border-border last:border-b-0">
                      <td className="px-4 py-3">
                        <Skeleton className="h-5 w-24" />
                      </td>
                      <td className="hidden px-4 py-3 sm:table-cell">
                        <Skeleton className="h-3 w-16" />
                      </td>
                      <td className="px-4 py-3">
                        <Skeleton className="h-5 w-20 rounded-full" />
                      </td>
                      <td className="hidden px-4 py-3 md:table-cell">
                        <Skeleton className="h-3 w-32" />
                      </td>
                      <td className="px-2 py-3 text-right">
                        <Skeleton className="ml-auto h-5 w-12" />
                      </td>
                    </tr>
                  ))
                : runs.map((run) => <RunRow key={run.run_id} run={run} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
