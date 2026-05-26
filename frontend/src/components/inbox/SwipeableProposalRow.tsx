/**
 * SwipeableProposalRow — ProposalRow wrapped with framer-motion drag for
 * mobile swipe-to-approve (right) and swipe-to-snooze (left).
 *
 * Swipe threshold: 80px. Only T1 proposals (tier <= 1) are swipe-approvable;
 * T2/T3/T4/HIPAA/dedup refuse with a toast.
 *
 * Shows emerald backdrop on right drag, sky blue on left drag.
 * Honors prefers-reduced-motion.
 */
import { useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Check, X } from "lucide-react";
import { toast } from "sonner";

import { actionVerbFor } from "@/components/inbox/ProposalRow";
import { AgentBadge } from "@/components/inbox/AgentBadge";
import { ConfidenceDots } from "@/components/inbox/ConfidenceDots";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Proposal } from "@/lib/types";
import { useApproveMutation, useSnoozeMutation, type InboxFilters } from "@/hooks/useInbox";
import { selectTier } from "@/lib/inbox/tierSelector";

const SWIPE_THRESHOLD = 80;

export type SwipeableProposalRowProps = {
  proposal: Proposal;
  focused?: boolean;
  selected?: boolean;
  onSelect?: () => void;
  onFocus?: () => void;
  onApprove?: () => void;
  onReject?: () => void;
};

export function SwipeableProposalRow({
  proposal,
  focused = false,
  selected = false,
  onSelect,
  onFocus,
  onApprove,
  onReject,
}: SwipeableProposalRowProps) {
  const prefersReduced = useReducedMotion();
  const tier = selectTier(proposal, { tenantAutoApprove: {} });
  const isSwipeApprovable = tier === 1 && !proposal.is_dedup;

  const filters: InboxFilters = { status: "proposed" };
  const approve = useApproveMutation(filters);
  const snooze = useSnoozeMutation(filters);

  const [dragX, setDragX] = useState(0);
  const verb = actionVerbFor(proposal.action_type, proposal);

  const backdropColor = useMemo(() => {
    if (dragX > 10) return "bg-emerald-500/20";
    if (dragX < -10) return "bg-sky-500/20";
    return "";
  }, [dragX]);

  function handleSwipeApprove() {
    if (tier === 4) {
      toast.message("Typed confirmation required", {
        description: "Open in detail pane to approve.",
      });
      return;
    }
    if (tier >= 2 || proposal.compliance.hipaa || proposal.is_dedup) {
      toast.message("Swipe approve not available", {
        description: "This proposal requires desktop review.",
      });
      return;
    }
    approve.mutate({
      proposalId: proposal.proposal_id,
      tier: 1,
      typedPhrase: null,
      keyboardPath: false,
    });
  }

  function handleSwipeSnooze() {
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(9, 0, 0, 0);
    snooze.mutate({
      proposalId: proposal.proposal_id,
      snoozeUntil: tomorrow,
      reason: "custom",
    });
  }

  return (
    <div className="relative overflow-hidden rounded-md">
      {/* Colored backdrops — revealed on drag */}
      <motion.div
        className={cn(
          "absolute inset-0 flex items-center justify-end px-4",
          backdropColor,
        )}
        style={{
          x: Math.max(0, dragX), // only show right backdrop when dragging right
        }}
        transition={{ duration: prefersReduced ? 0 : 0.15 }}
      >
        <Check className="h-6 w-6 text-emerald-600" />
      </motion.div>
      <motion.div
        className={cn(
          "absolute inset-0 flex items-center justify-start px-4",
          backdropColor,
        )}
        style={{
          x: Math.min(0, dragX), // only show left backdrop when dragging left
        }}
        transition={{ duration: prefersReduced ? 0 : 0.15 }}
      >
        <X className="h-6 w-6 text-sky-600" />
      </motion.div>

      {/* Draggable row */}
      <motion.div
        drag={isSwipeApprovable ? "x" : false}
        dragConstraints={{ left: -SWIPE_THRESHOLD * 2, right: SWIPE_THRESHOLD * 2 }}
        dragElastic={0.3}
        onDrag={(_event, info) => {
          setDragX(info.offset.x);
        }}
        onDragEnd={(_event, info) => {
          if (info.offset.x > SWIPE_THRESHOLD) {
            handleSwipeApprove();
          } else if (info.offset.x < -SWIPE_THRESHOLD) {
            handleSwipeSnooze();
          }
          // Snap back
          setDragX(0);
        }}
        transition={{ duration: prefersReduced ? 0 : 0.15 }}
        className={cn(
          "group flex items-center gap-3 rounded-md px-3 py-2 text-sm",
          "border-l-2 border-transparent",
          "transition-colors cursor-pointer",
          "hover:bg-muted/50",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2",
          focused && "bg-muted border-l-violet-500",
          selected && "bg-violet-500/10 border-l-violet-500",
        )}
        onClick={onSelect}
        onFocus={onFocus}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect?.();
          }
        }}
        role="button"
        tabIndex={0}
        data-proposal-id={proposal.proposal_id}
        aria-pressed={selected}
        aria-current={focused ? "true" : undefined}
      >
        <AgentBadge slug={proposal.agent_id} version={proposal.agent_version} variant="compact" />
        <span className="flex-1 truncate font-medium text-foreground/90">{verb}</span>
        <ConfidenceDots value={proposal.confidence} showLabel={false} size="sm" />
        <span className="hidden text-xs text-muted-foreground sm:inline">{/* created_at */}</span>
        <span className="ml-2 hidden items-center gap-1 group-hover:inline-flex">
          {isSwipeApprovable && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-emerald-500 hover:bg-emerald-500/10 hover:text-emerald-600"
              onClick={(e) => {
                e.stopPropagation();
                onApprove?.();
              }}
              title="Approve (Y)"
              aria-label="Approve proposal"
            >
              <Check className="h-3.5 w-3.5" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-rose-500 hover:bg-rose-500/10 hover:text-rose-600"
            onClick={(e) => {
              e.stopPropagation();
              onReject?.();
            }}
            title="Reject (N)"
            aria-label="Reject proposal"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </span>
      </motion.div>
    </div>
  );
}
