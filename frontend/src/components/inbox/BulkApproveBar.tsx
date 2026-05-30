/**
 * BulkApproveBar — filter-driven bulk action row in the /review toolbar.
 *
 * Distinct from the existing selection-driven BulkActionBar:
 *   - BulkActionBar fires on shift-click multi-select (selectionCount ≥ 2)
 *   - BulkApproveBar fires on the quality filter chips (matchedCount ≥ 1)
 *
 * Workflow when Aaron has 839 connector-pulled proposals: he picks "≥ 0.95
 * + Has email + From Gmail", the chip count says "412 match", he clicks
 * Approve all matching, types confirmation, and 412 single-approve calls
 * fire in parallel via `Promise.allSettled`. The progress line ticks up as
 * settlements arrive.
 *
 * Why `allSettled` over the existing single-approve hook (not the existing
 * `bulk_approve` MCP tool): the filter-driven approve has to gracefully
 * tolerate per-row failures (stale ETag, suppression rule, etc.) without
 * taking the whole batch down — which `bulk_approve`'s strict envelope
 * doesn't expose at the granularity Aaron needs for this workflow.
 *
 * Codex MAY ship `approve_proposals_bulk` later; the parent can swap in
 * that single call when it lands without changing this component's surface.
 */
import { useEffect, useId, useRef, useState } from "react";
import { CheckCircle2, ListFilter, Loader2, ThumbsDown, ThumbsUp } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { MonoNumeric } from "@/design-system";
import { cn } from "@/lib/utils";

type Mode = "approve" | "reject";

type BulkProgress = { settled: number; total: number; failed: number };

export type BulkApproveBarProps = {
  /** Number of proposals matching the quality filter right now. */
  matchedCount: number;
  /** Disable buttons during execution. */
  busy: boolean;
  progress: BulkProgress | null;
  onApproveAll: () => void | Promise<void>;
  onRejectAll: () => void | Promise<void>;
  /** Whether quality filter is in non-empty state — controls visibility. */
  visible: boolean;
};

export function BulkApproveBar({
  matchedCount,
  busy,
  progress,
  onApproveAll,
  onRejectAll,
  visible
}: BulkApproveBarProps) {
  const [confirmMode, setConfirmMode] = useState<Mode | null>(null);
  const approveBtnRef = useRef<HTMLButtonElement | null>(null);

  // `b` to focus the Bulk-Approve button. Hotkey scope-aware: only fires
  // when the bar is visible and there's at least one match.
  useEffect(() => {
    if (!visible || matchedCount === 0) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "b" && event.key !== "B") return;
      const target = event.target as HTMLElement | null;
      // Don't steal `b` from text inputs.
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      event.preventDefault();
      approveBtnRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, matchedCount]);

  if (!visible) return null;

  if (matchedCount === 0) {
    return (
      <div
        className="flex items-center gap-co-6 border-b border-border bg-card/25 px-co-16 py-co-8 text-13 text-muted-foreground"
        role="status"
        aria-live="polite"
      >
        <ListFilter className="h-3.5 w-3.5" />
        No proposals match these filters.
      </div>
    );
  }

  const handleClickConfirm = (mode: Mode) => setConfirmMode(mode);

  return (
    <div
      className="flex flex-wrap items-center gap-co-8 border-b border-border bg-card/55 px-co-16 py-co-8 shadow-[var(--shadow-1)]"
      role="region"
      aria-label="Bulk actions for filtered proposals"
    >
      <span className="inline-flex items-center gap-co-6 text-13 font-medium" aria-live="polite">
        <ListFilter className="h-3.5 w-3.5 text-muted-foreground" />
        <MonoNumeric tone="strong" data-testid="bulk-matched-count">
          {matchedCount}
        </MonoNumeric>
        <span>proposal{matchedCount === 1 ? "" : "s"} match</span>
      </span>

      {progress ? (
        <span
          className="inline-flex items-center gap-co-4 text-12 text-muted-foreground"
          aria-live="polite"
          data-testid="bulk-progress"
        >
          <Loader2 className={cn("h-3 w-3", progress.settled < progress.total && "animate-spin")} />
          <span>
            Settled <MonoNumeric tone="strong">{progress.settled}</MonoNumeric>/{progress.total}
          </span>
          {progress.failed > 0 ? (
            <span className="text-warning">· {progress.failed} failed</span>
          ) : null}
        </span>
      ) : null}

      <div className="ml-auto flex flex-wrap items-center gap-co-6">
        <Button
          ref={approveBtnRef}
          size="sm"
          variant="gradient"
          className="h-8 gap-1.5"
          disabled={busy}
          onClick={() => handleClickConfirm("approve")}
          data-testid="bulk-approve-button"
        >
          <ThumbsUp className="h-3.5 w-3.5" />
          Approve all matching
          <span className="ml-1 inline-flex items-center rounded bg-background/20 px-1.5 font-mono text-[10px]">
            B
          </span>
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
          disabled={busy}
          onClick={() => handleClickConfirm("reject")}
        >
          <ThumbsDown className="h-3.5 w-3.5" />
          Reject all matching
        </Button>
      </div>

      <BulkConfirmModal
        open={confirmMode != null}
        mode={confirmMode}
        count={matchedCount}
        progress={progress}
        busy={busy}
        onClose={() => setConfirmMode(null)}
        onConfirm={async () => {
          if (confirmMode === "approve") {
            await onApproveAll();
          } else if (confirmMode === "reject") {
            await onRejectAll();
          }
        }}
      />
    </div>
  );
}

type ConfirmModalProps = {
  open: boolean;
  mode: Mode | null;
  count: number;
  progress: BulkProgress | null;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
};

function BulkConfirmModal({ open, mode, count, progress, busy, onClose, onConfirm }: ConfirmModalProps) {
  const headingId = useId();
  // Auto-close when settlement reaches the total — the parent's toast carries
  // the success summary, so leaving the modal up would just block re-entry.
  useEffect(() => {
    if (!open || !progress) return;
    if (progress.settled >= progress.total) {
      const t = window.setTimeout(onClose, 700);
      return () => window.clearTimeout(t);
    }
  }, [open, progress, onClose]);

  if (mode == null) return null;

  const verb = mode === "approve" ? "approve" : "reject";
  const Verb = mode === "approve" ? "Approve" : "Reject";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent aria-labelledby={headingId}>
        <DialogHeader>
          <DialogTitle id={headingId} className="flex items-center gap-2 text-base">
            {mode === "approve" ? (
              <ThumbsUp className="h-4 w-4 text-success" />
            ) : (
              <ThumbsDown className="h-4 w-4 text-destructive" />
            )}
            {Verb} {count} proposal{count === 1 ? "" : "s"}?
          </DialogTitle>
          <DialogDescription>
            This will fire <span className="font-medium text-foreground">{count}</span> {verb} call
            {count === 1 ? "" : "s"} in parallel. Failures don&apos;t block the rest of the batch —
            the count of failed rows is reported in the success toast and on Recent activity.
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-md border border-border bg-muted/40 p-3 text-sm" data-testid="bulk-confirm-progress">
          <p className="flex items-center gap-2">
            {progress && progress.settled >= progress.total ? (
              <CheckCircle2 className="h-4 w-4 text-success" />
            ) : (
              <Loader2 className={cn("h-4 w-4", busy && "animate-spin")} />
            )}
            <span>
              Settled count:{" "}
              <span className="co-mono-numeric font-medium text-foreground">
                {progress?.settled ?? 0}
              </span>
              <span className="text-muted-foreground">/{progress?.total ?? count}</span>
              {progress && progress.failed > 0 ? (
                <span className="ml-2 text-warning">{progress.failed} failed</span>
              ) : null}
            </span>
          </p>
        </div>
        <DialogFooter>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            disabled={busy && progress != null && progress.settled < progress.total}
          >
            {progress && progress.settled >= progress.total ? "Close" : "Cancel"}
          </Button>
          <Button
            size="sm"
            variant={mode === "approve" ? "gradient" : "destructive"}
            disabled={busy}
            onClick={() => void onConfirm()}
          >
            {busy ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Working…
              </>
            ) : (
              <>
                {mode === "approve" ? (
                  <ThumbsUp className="h-3.5 w-3.5" />
                ) : (
                  <ThumbsDown className="h-3.5 w-3.5" />
                )}
                {Verb} {count}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
