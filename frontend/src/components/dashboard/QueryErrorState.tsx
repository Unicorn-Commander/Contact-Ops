/**
 * QueryErrorState — the shared "a data query failed" surface.
 *
 * Rendered on the main dashboards when a query fails with a NON-401 error, so a
 * backend outage reads as a recoverable error (with a Retry) instead of an
 * empty/zero state that quietly pretends there is simply no data.
 *
 * 401s never reach here — the data hooks redirect to login on Unauthorized (see
 * lib/queryState.ts `isRenderableQueryError`).
 */
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MCPError } from "@/lib/mcp";
import { cn } from "@/lib/utils";

export type QueryErrorStateProps = {
  /** Short headline; defaults to a generic "couldn't load" message. */
  title?: string;
  /** The query error, used to derive a human-readable detail line. */
  error?: unknown;
  /** Retry handler — typically `query.refetch`. */
  onRetry?: () => void;
  /** True while a retry is in flight (disables the button, spins the icon). */
  retrying?: boolean;
  /** Compact variant for inline card bodies (less vertical padding). */
  compact?: boolean;
  className?: string;
};

/** Best-effort human-readable detail from an unknown query error. */
export function errorDetail(error: unknown): string {
  if (error instanceof MCPError) {
    return typeof error.code === "number"
      ? `The server returned an error (HTTP ${error.code}).`
      : error.message || "The server returned an error.";
  }
  if (error instanceof Error && error.message) return error.message;
  return "Please check your connection and try again.";
}

export function QueryErrorState({
  title = "Couldn't load this",
  error,
  onRetry,
  retrying = false,
  compact = false,
  className
}: QueryErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center gap-2 text-center",
        compact ? "py-8" : "py-12",
        className
      )}
    >
      <AlertTriangle
        className="h-8 w-8 text-[oklch(var(--co-amber-500))]"
        strokeWidth={1.5}
        aria-hidden
      />
      <p className="text-sm font-medium text-foreground">{title}</p>
      <p className="max-w-xs text-xs text-muted-foreground">{errorDetail(error)}</p>
      {onRetry ? (
        <Button
          variant="outline"
          size="sm"
          className="mt-1"
          onClick={onRetry}
          disabled={retrying}
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", retrying && "animate-spin")}
            strokeWidth={1.8}
          />
          {retrying ? "Retrying…" : "Retry"}
        </Button>
      ) : null}
    </div>
  );
}
