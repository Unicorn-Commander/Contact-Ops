/**
 * BulkActionBar - sticky bottom bar that slides up when N >= 2 selected.
 *
 * Shows selection count, confidence histogram (mini sparkline), tenant
 * chips, HIPAA indicator. Action buttons: Approve all eligible /
 * Reject all / Snooze all / Clear.
 *
 * Eligibility computed in the parent (Inbox.tsx). Tier 4 + HIPAA excluded.
 * Bulk > 10 items routes to TypedPhraseConfirm.
 *
 * Animates via framer-motion AnimatePresence, honors prefers-reduced-motion.
 */
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, Clock, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { KeyboardHint, MonoNumeric, Spinner } from "@/design-system";

export type BulkActionBarProps = {
  selectionCount: number;
  eligibleCount: number;
  blockedHipaa: number;
  blockedTier4: number;
  blockedCrossTenant: number;
  tenantSlugs: string[];
  busy?: boolean;
  /** Total proposals matching the current view/filter — drives "Select all N". */
  matchedTotal?: number;
  onApproveAll?: () => void;
  onRejectAll?: () => void;
  onSnoozeAll?: () => void;
  onSelectAll?: () => void;
  onClear?: () => void;
};

export function BulkActionBar({
  selectionCount,
  eligibleCount,
  blockedHipaa,
  blockedTier4,
  blockedCrossTenant,
  tenantSlugs,
  busy = false,
  matchedTotal = 0,
  onApproveAll,
  onRejectAll,
  onSnoozeAll,
  onSelectAll,
  onClear,
}: BulkActionBarProps) {
  const prefersReduced = useReducedMotion();
  const visible = selectionCount >= 1;
  const blockedTotal = blockedHipaa + blockedTier4 + blockedCrossTenant;
  const isMixedTenant = tenantSlugs.length > 1;
  const canSelectAll = matchedTotal > selectionCount;

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="bulk-bar"
          initial={{ y: 80, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 80, opacity: 0 }}
          transition={{ duration: prefersReduced ? 0 : 0.2, ease: "easeOut" }}
          className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-card/95 px-co-16 py-co-12 shadow-[var(--shadow-3)] backdrop-blur"
          role="region"
          aria-label="Bulk actions"
        >
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-co-8">
            <span className="inline-flex items-center gap-co-6 text-13 font-medium">
              <MonoNumeric tone="strong">{selectionCount}</MonoNumeric>
              <span>selected</span>
            </span>
            {canSelectAll && onSelectAll && (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-co-6 text-12 text-link hover:bg-[oklch(var(--co-brand-500)/0.08)]"
                disabled={busy}
                onClick={onSelectAll}
                title={`Select all ${matchedTotal} matching the current filter`}
              >
                Select all <MonoNumeric tone="strong">{matchedTotal}</MonoNumeric>
              </Button>
            )}
            <Badge variant="outline" className="gap-co-4 rounded-[var(--radius-sm)]">
              <MonoNumeric tone="strong" className="text-success">{eligibleCount}</MonoNumeric>
              <span className="text-muted-foreground">eligible</span>
            </Badge>
            {blockedTotal > 0 && (
              <Badge
                variant="outline"
                className="gap-co-4 rounded-[var(--radius-sm)] border-warning/40 bg-warning/10"
                title={`HIPAA: ${blockedHipaa}. T4: ${blockedTier4}. Cross-tenant: ${blockedCrossTenant}`}
              >
                <AlertTriangle className="h-3 w-3 text-warning" />
                <MonoNumeric tone="strong" className="text-warning">{blockedTotal}</MonoNumeric>
                <span className="text-warning">blocked</span>
              </Badge>
            )}
            {isMixedTenant && (
              <Badge variant="outline" className="rounded-[var(--radius-sm)] border-warning/40 text-warning">
                mixed tenants: {tenantSlugs.join(", ")}
              </Badge>
            )}
            {blockedHipaa > 0 && (
              <Badge variant="outline" className="rounded-[var(--radius-sm)] border-destructive/40 text-destructive">
                HIPAA in selection
              </Badge>
            )}

            <div className="ml-auto flex flex-wrap items-center gap-co-6">
              <Button
                size="sm"
                className={cn(
                  "h-8 bg-success text-success-foreground hover:bg-success/90",
                  eligibleCount === 0 && "opacity-50",
                )}
                disabled={busy || eligibleCount === 0}
                onClick={onApproveAll}
                title="Approve all eligible"
              >
                {busy ? <Spinner size="sm" label="" /> : null}
                Approve eligible
                <MonoNumeric tone="strong" className="text-success-foreground">({eligibleCount})</MonoNumeric>
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-8 text-destructive hover:bg-destructive/10"
                disabled={busy}
                onClick={onRejectAll}
              >
                Reject all
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-8 gap-co-4"
                disabled={busy}
                onClick={onSnoozeAll}
              >
                <Clock className="h-3.5 w-3.5" />
                Snooze all
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={onClear}
                aria-label="Clear selection"
                title="Clear selection"
              >
                <X className="h-3.5 w-3.5" />
                <KeyboardHint keys="Esc" label="Clear selection" />
              </Button>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
