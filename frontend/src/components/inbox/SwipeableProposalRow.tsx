/**
 * SwipeableProposalRow - the v2 calm proposal row wrapped with framer-motion
 * drag for mobile swipe-to-approve (right) and swipe-to-snooze (left).
 *
 * Swipe threshold: 80px. Only T1 proposals (tier <= 1, non-dedup) are
 * swipe-approvable; T2/T3/T4/HIPAA/dedup refuse with a toast. The inline ✓ is
 * hidden for non-swipe-approvable rows (the reject ✗ stays), matching the prior
 * behavior — the shared ProposalRowBody takes `showApprove` for exactly this.
 *
 * Shows emerald backdrop on right drag, sky/info on left drag. Honors
 * prefers-reduced-motion. The row's visual language is identical to the desktop
 * ProposalRow because both render ProposalRowBody.
 */
import { useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Check, Clock } from "lucide-react";
import { toast } from "sonner";

import { ProposalRowBody, agentRowStyle } from "@/components/inbox/proposalRowShared";
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

  const backdropTone = useMemo(() => {
    if (dragX > 10) return "bg-success/15";
    if (dragX < -10) return "bg-info/15";
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
      {/* Colored backdrops revealed on drag. */}
      <motion.div
        className={cn(
          "absolute inset-0 flex items-center justify-end px-4",
          backdropTone,
        )}
        style={{
          x: Math.max(0, dragX), // only show right backdrop when dragging right
        }}
        transition={{ duration: prefersReduced ? 0 : 0.15 }}
      >
        <Check className="h-6 w-6 text-success" />
      </motion.div>
      <motion.div
        className={cn(
          "absolute inset-0 flex items-center justify-start px-4",
          backdropTone,
        )}
        style={{
          x: Math.min(0, dragX), // only show left backdrop when dragging left
        }}
        transition={{ duration: prefersReduced ? 0 : 0.15 }}
      >
        <Clock className="h-6 w-6 text-info" />
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
        style={agentRowStyle(proposal.agent_id)}
        className={cn(
          "co-v2-row co-v2-row-rail group grid min-h-12 cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-co-8 bg-card px-co-8 py-co-6 text-13",
          "focus-ring",
          focused && "border-[oklch(var(--co-brand-300)/0.5)] bg-[oklch(var(--co-brand-500)/0.08)] shadow-[var(--shadow-1)]",
          selected && "border-primary bg-primary/15",
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
        <ProposalRowBody
          proposal={proposal}
          onApprove={onApprove}
          onReject={onReject}
          showApprove={isSwipeApprovable}
        />
      </motion.div>
    </div>
  );
}
