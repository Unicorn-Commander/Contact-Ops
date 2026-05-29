/**
 * ProviderCard — one shared card shell rendering a connector for a single
 * provider (iCloud / M365 / Gmail). Owns the status badge, status-coloured
 * left rail, header (icon + name + status), body description / last-pull
 * summary, and the bottom action row. Action buttons themselves come from the
 * caller so each provider can wire its own "Connect" / "Pull now" intent.
 *
 * Status → tone map:
 *   not configured (no row)  → sky    "Not configured"
 *   configured                → sky    "Configured"     (creds saved, no pulls)
 *   connected                 → emerald "Connected"     (at least one success)
 *   disconnected              → muted  "Disconnected"
 *   error                     → rose   "Error"          + last_error text
 *
 * The card is fully presentational. It honors prefers-reduced-motion via the
 * surrounding `co-animate-fade-in` utility (added by the page). Tone follows
 * Contact-Ops canon: card surface, brand purple primary, semantic accents.
 */
import { type ReactNode } from "react";
import { AlertCircle, CheckCircle2, HelpCircle, PlugZap, Sparkles } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ConnectorProvider, ConnectorRunSummary, ConnectorStatus } from "@/lib/types";

/**
 * Auth-shape hints — surfaced beneath the provider name in the Not configured
 * state so the user knows whether to brace for an app-specific password vs.
 * a sign-in popup before clicking Connect. Copy is intentionally tiny and
 * verb-led; the deep walkthrough lives on /help/connectors.
 */
export const PROVIDER_AUTH_HINT: Record<ConnectorProvider, string> = {
  icloud: "Apple ID + an app-specific password",
  m365: "Sign in with Microsoft (read-only contacts)",
  gmail: "Sign in with Google (read-only contacts)"
};

/**
 * Subtle context line — same audience as PROVIDER_AUTH_HINT but placed in
 * the action area on the per-card slot in Connectors.tsx. iCloud reuses the
 * modal disclosure (so we return null); OAuth providers get a single muted
 * sentence so the popup isn't a surprise.
 */
export const PROVIDER_ACTION_HINT: Partial<Record<ConnectorProvider, string>> = {
  m365: "Opens Microsoft sign-in in a popup. You'll grant Contact-Ops read-only access to your contacts.",
  gmail: "Opens Google sign-in in a popup. You'll grant Contact-Ops read-only access to your contacts."
};

// ---- Status tone map -------------------------------------------------------

export type ProviderCardStatus = ConnectorStatus | "not_configured";

interface ToneSpec {
  /** Badge text. */
  label: string;
  /** OKLCH variable name suffix (used to color the dot + label + rail). */
  tone: "emerald" | "sky" | "rose" | "amber" | "muted";
}

const STATUS_TONE: Record<ProviderCardStatus, ToneSpec> = {
  not_configured: { label: "Not configured", tone: "muted" },
  configured: { label: "Configured", tone: "sky" },
  connected: { label: "Connected", tone: "emerald" },
  disconnected: { label: "Disconnected", tone: "muted" },
  error: { label: "Error", tone: "rose" }
};

function StatusBadge({ status }: { status: ProviderCardStatus }) {
  const spec = STATUS_TONE[status];
  // Use OKLCH custom-property tints so dark + light both clear AA contrast.
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
      aria-label={`Status: ${spec.label}`}
    >
      <span className={cn("inline-block h-1.5 w-1.5 rounded-full", dotClass[spec.tone])} aria-hidden="true" />
      {spec.label}
    </span>
  );
}

void Badge; // kept import path consistent with the rest of /ui

// ---- Last-pull summary chip + error banner ---------------------------------

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

interface LastPullProps {
  lastPullAt?: string | null;
  summary?: ConnectorRunSummary | null;
  isPulling?: boolean;
}

function LastPullChip({ lastPullAt, summary, isPulling }: LastPullProps) {
  if (isPulling) {
    return (
      <p
        className="flex items-center gap-2 text-xs text-muted-foreground"
        aria-live="polite"
      >
        <span
          className="inline-flex h-1.5 w-1.5 rounded-full bg-[oklch(var(--co-sky-500))] co-animate-pulse-subtle"
          aria-hidden="true"
        />
        Pulling now…
      </p>
    );
  }
  if (!lastPullAt) {
    return null;
  }
  const proposed = summary?.proposed_count;
  return (
    <p className="text-xs text-muted-foreground">
      Last pull: <span className="text-foreground/85">{formatRelativeTime(lastPullAt)}</span>
      {typeof proposed === "number" ? (
        <>
          {" "}
          ·{" "}
          <span className="font-medium text-foreground">{proposed.toLocaleString()}</span>{" "}
          proposed
        </>
      ) : null}
    </p>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-md border border-[oklch(var(--co-rose-500)/0.35)] bg-[oklch(var(--co-rose-500)/0.06)] px-3 py-2 text-xs leading-snug text-foreground"
    >
      <AlertCircle
        className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[oklch(var(--co-rose-500))]"
        strokeWidth={2}
        aria-hidden="true"
      />
      <span className="min-w-0">{message}</span>
    </div>
  );
}

// ---- Card shell ------------------------------------------------------------

export interface ProviderCardProps {
  /** Provider display name shown in the title row (e.g. "iCloud"). */
  name: string;
  /** Short description below the title — describes what the connector pulls. */
  description: string;
  /** Pre-mounted provider icon (lucide or inline SVG). Color comes from the parent. */
  icon: ReactNode;
  /** Status drives the side rail, badge, and which action row variant the page renders. */
  status: ProviderCardStatus;
  /** ISO 8601; renders "Last pull: 2 hours ago" when set. */
  lastPullAt?: string | null;
  /** Inline counts from the last successful pull (proposed_count shown). */
  lastPullSummary?: ConnectorRunSummary | null;
  /** Backend-formatted reason — surfaced verbatim in the Error state banner. */
  lastError?: string | null;
  /** Tone of the icon tile + left rail; matches the brand for connected. */
  accent?: "brand" | "fuchsia" | "neutral";
  /** Show the small "pulling now" indicator regardless of status. */
  isPulling?: boolean;
  /** Action row content — Connect / Pull now / Disconnect buttons. */
  actions: ReactNode;
  /** Extra slot above the actions (e.g. a "Run with Preview only" toggle/menu). */
  toolbar?: ReactNode;
  /**
   * Provider key — when set together with `status === "not_configured"`, the
   * card renders the auth-shape hint beneath the name and a "Learn more" link
   * to the per-provider help-page anchor. Optional so the card stays purely
   * presentational for callers that don't need the hint.
   */
  provider?: ConnectorProvider;
  /**
   * Optional sub-text rendered above the action row in the Not configured
   * state — used by the page to surface the OAuth-popup explanation. Kept
   * deliberately small + muted so it never competes with the primary CTA.
   */
  actionHint?: string | null;
}

export function ProviderCard({
  name,
  description,
  icon,
  status,
  lastPullAt,
  lastPullSummary,
  lastError,
  accent = "brand",
  isPulling,
  actions,
  toolbar,
  provider,
  actionHint
}: ProviderCardProps) {
  // The status-coloured rail lives outside the card body so it's flush.
  const railClass: Record<ProviderCardStatus, string> = {
    not_configured: "bg-muted-foreground/30",
    configured: "bg-[oklch(var(--co-sky-500))]",
    connected: "bg-[oklch(var(--co-emerald-500))]",
    disconnected: "bg-muted-foreground/40",
    error: "bg-[oklch(var(--co-rose-500))]"
  };
  const iconTileClass: Record<typeof accent, string> = {
    brand: "bg-gradient-brand text-primary-foreground shadow-[var(--shadow-1)]",
    fuchsia:
      "bg-[oklch(var(--co-fuchsia-500)/0.18)] text-[oklch(var(--co-fuchsia-500))]",
    neutral: "bg-muted text-foreground"
  };

  return (
    <Card
      className={cn(
        "co-animate-fade-in relative overflow-hidden",
        status === "error" && "border-[oklch(var(--co-rose-500)/0.45)]"
      )}
    >
      {/* Left status rail */}
      <span
        aria-hidden="true"
        className={cn("absolute left-0 top-0 h-full w-[3px]", railClass[status])}
      />
      <div className="flex flex-col gap-4 p-5 pl-6">
        {/* Header — icon + name + status */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span
              className={cn(
                "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl",
                iconTileClass[accent]
              )}
              aria-hidden="true"
            >
              {icon}
            </span>
            <div className="min-w-0">
              <h3 className="text-base font-semibold leading-tight tracking-tight">{name}</h3>
              {status === "not_configured" && provider ? (
                <p className="mt-0.5 text-[11px] font-medium text-foreground/80">
                  {PROVIDER_AUTH_HINT[provider]}
                </p>
              ) : null}
              <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
            </div>
          </div>
          <StatusBadge status={status} />
        </div>

        {/* Body — last-pull chip, error banner, or "not configured" hint */}
        <div className="space-y-2">
          {status === "error" && lastError ? <ErrorBanner message={lastError} /> : null}
          {(status === "connected" || status === "configured") && !lastError ? (
            <LastPullChip
              lastPullAt={lastPullAt}
              summary={lastPullSummary}
              isPulling={isPulling}
            />
          ) : null}
          {status === "not_configured" ? (
            <div className="space-y-1.5">
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <PlugZap className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden="true" />
                Not connected yet
              </p>
              {actionHint ? (
                <p className="text-[11px] leading-snug text-muted-foreground">{actionHint}</p>
              ) : null}
              {provider ? (
                <p className="flex items-center gap-1 text-[11px]">
                  <HelpCircle
                    className="h-3 w-3 text-muted-foreground"
                    strokeWidth={1.8}
                    aria-hidden="true"
                  />
                  <a
                    href={`/help/connectors#${provider}`}
                    className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
                  >
                    Learn more
                  </a>
                </p>
              ) : null}
            </div>
          ) : null}
          {status === "disconnected" ? (
            <p className="text-xs text-muted-foreground">
              Disconnected. Credentials removed; re-connect to resume pulls.
            </p>
          ) : null}
          {status === "configured" && !lastPullAt && !lastError ? (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Sparkles className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden="true" />
              Ready for first pull
            </p>
          ) : null}
          {toolbar}
        </div>

        {/* Action row */}
        <div className="flex flex-wrap items-center justify-end gap-2">{actions}</div>
      </div>
    </Card>
  );
}

void CheckCircle2; // legacy keep — used in the connect-success banner variant
