/**
 * ProposalRow - single proposal inside a cluster.
 *
 * Renders agent attribution, action verb, confidence, and inline quick
 * actions (✓ approve / ✗ reject) revealed on hover. Click anywhere
 * (except the inline buttons) opens the detail pane.
 *
 * Keyboard focus is managed by the parent (Inbox.tsx) via J/K, which
 * sets `focused` and scrolls into view.
 */
import { Check, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Proposal } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  ActionAttribution,
  AgentBadge,
  ConfidenceIndicator,
  KeyboardHint,
} from "@/design-system";

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
  if (p.is_dedup) return "Merge with...";
  if (actionType.startsWith("tag.add")) return "Tag...";
  if (actionType.startsWith("tag.remove")) return "Remove tag";
  if (actionType.startsWith("lifecycle.")) return "Lifecycle...";
  if (actionType.startsWith("enrichment.")) {
    const fields = Object.keys(p.payload_after);
    if (fields.length === 1) return `Set ${fields[0]}=...`;
    return `Set ${fields.length} fields`;
  }
  if (actionType.startsWith("relationship.")) return "Link relationship";
  if (actionType.startsWith("voice_match.")) return "Match voice...";
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
  const agentLabel = `${proposal.agent_id} ${proposal.agent_version}`;

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
        "group grid min-h-12 cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-co-8 rounded-md border border-transparent px-co-8 py-co-6 text-13",
        "transition-colors duration-150 ease-snappy hover:border-border hover:bg-muted/70",
        "focus-ring",
        focused && "border-primary/55 bg-primary/10 shadow-[var(--shadow-1)]",
        selected && "border-primary bg-primary/15",
      )}
      aria-pressed={selected}
      aria-current={focused ? "true" : undefined}
    >
      <AgentBadge
        slug={proposal.agent_id}
        label={agentLabel}
        size="xs"
        showLabel={false}
      />
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-co-6">
          <span className="truncate font-medium text-foreground" title={verb}>
            {verb}
          </span>
          {proposal.cross_tenant ? (
            <span className="rounded-[var(--radius-sm)] border border-warning/35 bg-warning/10 px-co-4 py-co-2 font-mono text-11 text-warning">
              cross-tenant
            </span>
          ) : null}
          {proposal.compliance.hipaa ? (
            <span className="rounded-[var(--radius-sm)] border border-destructive/35 bg-destructive/10 px-co-4 py-co-2 font-mono text-11 text-destructive">
              HIPAA
            </span>
          ) : null}
        </div>
        <ActionAttribution
          actor={{ type: "agent", slug: proposal.agent_id, label: agentLabel }}
          verb="Proposed"
          timestamp={proposal.created_at}
          compact
          className="mt-co-2 hidden min-w-0 sm:inline-flex"
        />
      </div>
      <ConfidenceIndicator value={proposal.confidence} mode="binary" showLabel={false} size="sm" />
      <span className="ml-co-4 hidden items-center gap-co-4 group-hover:inline-flex group-focus-within:inline-flex">
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-success hover:bg-success/10 hover:text-success"
          onClick={(e) => {
            e.stopPropagation();
            onApprove?.();
          }}
          title="Approve (Y)"
          aria-label="Approve proposal"
        >
          <Check className="h-3.5 w-3.5" />
          <span className="sr-only">Approve</span>
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-destructive hover:bg-destructive/10 hover:text-destructive"
          onClick={(e) => {
            e.stopPropagation();
            onReject?.();
          }}
          title="Reject (N)"
          aria-label="Reject proposal"
        >
          <X className="h-3.5 w-3.5" />
          <span className="sr-only">Reject</span>
        </Button>
        <span className="hidden items-center gap-co-2 xl:inline-flex" aria-hidden="true">
          <KeyboardHint keys="Y" label="Approve focused proposal" />
          <KeyboardHint keys="N" label="Reject focused proposal" />
        </span>
      </span>
    </div>
  );
}
