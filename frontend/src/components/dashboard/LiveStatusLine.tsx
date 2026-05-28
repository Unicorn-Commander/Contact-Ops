/**
 * Date + live-status line that sits under the dashboard greeting.
 *
 * "Tuesday, May 27 · Live" with a small pulsing dot when the workspace is
 * actively syncing/connected, or a steady muted dot when idle. The pulse uses
 * the token `.co-animate-pulse-subtle` so it respects prefers-reduced-motion.
 */
import { cn } from "@/lib/utils";

export interface LiveStatusLineProps {
  /** Defaults to now; injectable for deterministic previews/tests. */
  date?: Date;
  /** Live = green pulsing dot; otherwise a steady muted dot. */
  live?: boolean;
  /** Status word shown after the date, e.g. "Live" / "Syncing" / "Idle". */
  statusLabel?: string;
  className?: string;
}

const FMT = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" });

export function LiveStatusLine({ date = new Date(), live = true, statusLabel, className }: LiveStatusLineProps) {
  const dateText = FMT.format(date);
  const status = statusLabel ?? (live ? "Live" : "Idle");
  return (
    <span className={cn("inline-flex items-center gap-2 text-xs text-muted-foreground", className)}>
      <span className="tabular-nums">{dateText}</span>
      <span className="text-border" aria-hidden>
        ·
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            live ? "co-animate-pulse-subtle bg-[oklch(var(--co-emerald-500))]" : "bg-muted-foreground"
          )}
          aria-hidden
        />
        <span className={cn(live && "text-[oklch(var(--co-emerald-500))]")}>{status}</span>
      </span>
    </span>
  );
}
