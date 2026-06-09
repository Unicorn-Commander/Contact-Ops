/**
 * ProposalRow - single proposal inside a cluster (Design Language v2).
 *
 * Renders the approved "calm proposal row": agent sigil + leading rail, a verb
 * badge + plain-English summary, agent attribution (name · version · proposed
 * Xago), a calm color-coded confidence meter, inline context tags
 * (cross-tenant / HIPAA), and ALWAYS-VISIBLE ✓ approve / ✗ reject. Click
 * anywhere (except those buttons) opens the detail pane.
 *
 * The row's *presentation* lives in `proposalRowShared` (ProposalRowBody) so
 * the desktop row and the mobile SwipeableProposalRow stay visually identical.
 * This component owns only the interaction wrapper (focus / selection /
 * click-to-detail / keyboard), which is unchanged from the prior version.
 *
 * Keyboard focus is managed by the parent (Inbox.tsx) via J/K, which sets
 * `focused` and scrolls into view (relies on data-proposal-id below).
 */
import { cn } from "@/lib/utils";
import type { Proposal } from "@/lib/types";
import {
  ProposalRowBody,
  actionVerbFor,
  agentRowStyle,
} from "@/components/inbox/proposalRowShared";

// Re-exported for back-compat: SwipeableProposalRow and any future callers
// import `actionVerbFor` from here.
export { actionVerbFor };

/**
 * Modifier flags forwarded from a row click / Enter-Space so the parent can
 * implement Linear/Gmail-style range + toggle selection:
 *   plain   → open detail (focus)
 *   ⌘/Ctrl  → toggle this row in the multi-select set
 *   Shift   → select the contiguous range from the anchor to this row
 */
export type RowClickMeta = { shiftKey: boolean; metaKey: boolean; ctrlKey: boolean };

export type ProposalRowProps = {
  proposal: Proposal;
  focused?: boolean;
  selected?: boolean;
  onSelect?: (meta: RowClickMeta) => void;
  onFocus?: () => void;
  onApprove?: () => void;
  onReject?: () => void;
};

export function ProposalRow({
  proposal,
  focused = false,
  selected = false,
  onSelect,
  onFocus,
  onApprove,
  onReject,
}: ProposalRowProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      data-proposal-id={proposal.proposal_id}
      onClick={(e) =>
        onSelect?.({ shiftKey: e.shiftKey, metaKey: e.metaKey, ctrlKey: e.ctrlKey })
      }
      onFocus={onFocus}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.({ shiftKey: e.shiftKey, metaKey: e.metaKey, ctrlKey: e.ctrlKey });
        }
      }}
      style={agentRowStyle(proposal.agent_id)}
      className={cn(
        "co-v2-row co-v2-row-rail group grid min-h-12 cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-co-8 px-co-8 py-co-6 text-13",
        "focus-ring",
        focused && "border-[oklch(var(--co-brand-300)/0.5)] bg-[oklch(var(--co-brand-500)/0.08)] shadow-[var(--shadow-1)]",
        selected && "border-primary bg-primary/15",
      )}
      aria-pressed={selected}
      aria-current={focused ? "true" : undefined}
    >
      <ProposalRowBody proposal={proposal} onApprove={onApprove} onReject={onReject} />
    </div>
  );
}
