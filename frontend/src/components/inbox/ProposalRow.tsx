/**
 * ProposalRow — single proposal inside a cluster.
 *
 * Renders agent badge, action verb, ConfidenceDots, and inline quick
 * actions (✓ approve / ✗ reject) revealed on hover. Click anywhere
 * (except the inline buttons) opens the detail pane.
 *
 * Keyboard focus is managed by the parent (Inbox.tsx) via J/K, which
 * sets `focused` and scrolls into view.
 */
import { Check, X } from "lucide-react";
import { formatDistanceToNowStrict } from "date-fns";
import { cn } from "@/lib/utils";
import type { Proposal } from "@/lib/types";
import { AgentBadge } from "@/components/inbox/AgentBadge";
import { ConfidenceDots } from "@/components/inbox/ConfidenceDots";
import { Button } from "@/components/ui/button";

export type ProposalRowProps = {
  proposal: Proposal;
  focused?: boolean;
  selected?: boolean;
  onSelect?: () => void;
  onFocus?: () => void;
  onApprove?: () => void;
  onReject?: () => void;
};

export function actionVerbFor(actionType: string, p: Proposal): string {
  if (p.is_dedup) return "Merge with…";
  if (actionType.startsWith("tag.add")) return "Tag…";
  if (actionType.startsWith("tag.remove")) return "Remove tag";
  if (actionType.startsWith("lifecycle.")) return "Lifecycle…";
  if (actionType.startsWith("enrichment.")) {
    const fields = Object.keys(p.payload_after);
    if (fields.length === 1) return `Set ${fields[0]}=…`;
    return `Set ${fields.length} fields`;
  }
  if (actionType.startsWith("relationship.")) return "Link relationship";
  if (actionType.startsWith("voice_match.")) return "Match voice…";
  if (actionType.startsWith("carddav.")) return "Reconcile CardDAV";
  if (p.is_delete) return `Delete ${p.entity_kind}`;
  return actionType.replace(/_/g, " ").replace(/\./g, ": ");
}

export function ProposalRow({
  proposal,
  focused = false,
  selected = false,
  onSelect,
  onFocus,
  onApprove,
  onReject,
}: ProposalRowProps) {
  const verb = actionVerbFor(proposal.action_type, proposal);
  const created = formatDistanceToNowStrict(new Date(proposal.created_at), { addSuffix: true });

  return (
    <div
      role="button"
      tabIndex={0}
      data-proposal-id={proposal.proposal_id}
      onClick={onSelect}
      onFocus={onFocus}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.();
        }
      }}
      className={cn(
        "group flex items-center gap-3 rounded-md px-3 py-2 text-sm",
        "border-l-2 border-transparent",
        "transition-colors cursor-pointer",
        "hover:bg-muted/50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2",
        focused && "bg-muted border-l-violet-500",
        selected && "bg-violet-500/10 border-l-violet-500",
      )}
      aria-pressed={selected}
      aria-current={focused ? "true" : undefined}
    >
      <AgentBadge slug={proposal.agent_id} version={proposal.agent_version} variant="compact" />
      <span className="flex-1 truncate font-medium text-foreground/90">{verb}</span>
      <ConfidenceDots value={proposal.confidence} showLabel={false} size="sm" />
      <span className="hidden text-xs text-muted-foreground sm:inline">{created}</span>
      <span className="ml-2 hidden items-center gap-1 group-hover:inline-flex">
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
    </div>
  );
}
