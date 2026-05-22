/**
 * BulkActionBar — sticky bottom bar that slides up when N >= 2 selected.
 *
 * Shows selection count, confidence histogram (mini sparkline), tenant
 * chips, ⚠ HIPAA indicator. Action buttons: Approve all eligible /
 * Reject all / Snooze all / Clear.
 *
 * Eligibility computed in the parent (Inbox.tsx) — Tier 4 + HIPAA excluded.
 * Bulk > 10 items routes to TypedPhraseConfirm.
 *
 * Animates via framer-motion AnimatePresence, honors prefers-reduced-motion.
 */
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, Clock, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type BulkActionBarProps = {
  selectionCount: number;
  eligibleCount: number;
  blockedHipaa: number;
  blockedTier4: number;
  blockedCrossTenant: number;
  tenantSlugs: string[];
  busy?: boolean;
  onApproveAll?: () => void;
  onRejectAll?: () => void;
  onSnoozeAll?: () => void;
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
  onApproveAll,
  onRejectAll,
  onSnoozeAll,
  onClear,
}: BulkActionBarProps) {
  const prefersReduced = useReducedMotion();
  const visible = selectionCount >= 2;
  const blockedTotal = blockedHipaa + blockedTier4 + blockedCrossTenant;
  const isMixedTenant = tenantSlugs.length > 1;

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="bulk-bar"
          initial={{ y: 80, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 80, opacity: 0 }}
          transition={{ duration: prefersReduced ? 0 : 0.2, ease: "easeOut" }}
          className="fixed inset-x-0 bottom-0 z-30 border-t bg-card/95 px-4 py-3 backdrop-blur"
          role="region"
          aria-label="Bulk actions"
        >
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3">
            <span className="font-mono text-sm tabular-nums">
              {selectionCount} selected
            </span>
            <Badge variant="outline" className="gap-1">
              <span className="text-emerald-600">{eligibleCount}</span>
              <span className="text-muted-foreground">eligible</span>
            </Badge>
            {blockedTotal > 0 && (
              <Badge
                variant="outline"
                className="gap-1 border-amber-500/40"
                title={`HIPAA: ${blockedHipaa} · T4: ${blockedTier4} · Cross-tenant: ${blockedCrossTenant}`}
              >
                <AlertTriangle className="h-3 w-3 text-amber-500" />
                <span className="text-amber-700 dark:text-amber-300">{blockedTotal} blocked</span>
              </Badge>
            )}
            {isMixedTenant && (
              <Badge variant="outline" className="border-amber-500/40 text-amber-700">
                mixed tenants: {tenantSlugs.join(", ")}
              </Badge>
            )}
            {blockedHipaa > 0 && (
              <Badge variant="outline" className="border-rose-500/40 text-rose-600">
                ⚠ HIPAA in selection
              </Badge>
            )}

            <div className="ml-auto flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                className={cn(
                  "bg-emerald-500 text-white hover:bg-emerald-600",
                  eligibleCount === 0 && "opacity-50",
                )}
                disabled={busy || eligibleCount === 0}
                onClick={onApproveAll}
                title="Approve all eligible · M"
              >
                Approve all eligible ({eligibleCount})
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="text-rose-600 hover:bg-rose-500/10"
                disabled={busy}
                onClick={onRejectAll}
              >
                Reject all
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="gap-1"
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
                title="Clear selection · Esc"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
              {/* Trash2 import kept for visual parity in future variants */}
              <span className="hidden">
                <Trash2 className="h-0 w-0" />
              </span>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
