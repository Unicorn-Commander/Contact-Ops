/**
 * ClusterCard — one card per entity (or per dedup cluster, per agent
 * batch, etc., depending on cluster_kind).
 *
 * Header: avatar, entity name, N proposals, cumulative confidence avg
 *   as compact ConfidenceDots, row of compact AgentBadges, relative time,
 *   expand/collapse chevron.
 * Body (expanded): 1-N ProposalRows.
 * Footer: [Approve all ≥0.85] [Reject all] [Open detail].
 *
 * framer-motion `AnimatePresence` animates the expand/collapse. Honors
 * prefers-reduced-motion via framer-motion's useReducedMotion (set
 * duration to 0 when reduced).
 */
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, ChevronRight } from "lucide-react";
import { formatDistanceToNowStrict } from "date-fns";
import { cn, initials } from "@/lib/utils";
import type { Proposal, ProposalCluster } from "@/lib/types";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { AgentBadge } from "@/components/inbox/AgentBadge";
import { ConfidenceDots } from "@/components/inbox/ConfidenceDots";
import { ProposalRow } from "@/components/inbox/ProposalRow";
import { SwipeableProposalRow } from "@/components/inbox/SwipeableProposalRow";

const MAX_INLINE = 12;

export type ClusterCardProps = {
  cluster: ProposalCluster;
  proposals: Proposal[];
  expanded: boolean;
  onToggle: () => void;
  focusedProposalId?: string | null;
  selectedProposalIds?: Set<string>;
  onSelectProposal?: (proposal: Proposal) => void;
  onFocusProposal?: (proposal: Proposal) => void;
  onQuickApprove?: (proposal: Proposal) => void;
  onQuickReject?: (proposal: Proposal) => void;
  onApproveAll?: () => void;
  onRejectAll?: () => void;
  onOpenDetail?: () => void;
  /** When viewing across tenants, show the tenant badge on each card. */
  showTenantBadge?: boolean;
  tenantSlug?: string;
  hasConflict?: boolean;
  /** When true, render SwipeableProposalRow for mobile swipe gestures. */
  mobileSwipe?: boolean;
};

export function ClusterCard({
  cluster,
  proposals,
  expanded,
  onToggle,
  focusedProposalId,
  selectedProposalIds,
  onSelectProposal,
  onFocusProposal,
  onQuickApprove,
  onQuickReject,
  onApproveAll,
  onRejectAll,
  onOpenDetail,
  showTenantBadge = false,
  tenantSlug,
  hasConflict = false,
  mobileSwipe = false,
}: ClusterCardProps) {
  const prefersReduced = useReducedMotion();
  const visible = proposals.slice(0, MAX_INLINE);
  const overflow = Math.max(0, proposals.length - MAX_INLINE);
  const created = formatDistanceToNowStrict(new Date(cluster.latest_created_at), { addSuffix: true });
  const eligibleCount = proposals.filter((p) => p.confidence >= 0.85).length;

  return (
    <Card className="overflow-hidden">
      <header
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        className={cn(
          "flex items-center gap-3 p-3",
          "cursor-pointer select-none",
          "hover:bg-muted/40",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-inset",
        )}
        aria-expanded={expanded}
        aria-label={`Cluster for ${cluster.entity_display_name}, ${proposals.length} proposals`}
      >
        <ChevronRight
          className={cn(
            "h-4 w-4 text-muted-foreground transition-transform",
            expanded && "rotate-90",
            prefersReduced && "transition-none",
          )}
          aria-hidden="true"
        />
        <Avatar className="h-8 w-8">
          {cluster.entity_avatar_url ? (
            <AvatarImage src={cluster.entity_avatar_url} alt={cluster.entity_display_name} />
          ) : null}
          <AvatarFallback>{initials(cluster.entity_display_name)}</AvatarFallback>
        </Avatar>
        <div className="flex flex-1 items-center gap-2 min-w-0">
          <span className="truncate font-semibold text-foreground/90" title={cluster.entity_display_name}>
            {cluster.entity_display_name.length > 40
              ? cluster.entity_display_name.slice(0, 40) + "…"
              : cluster.entity_display_name}
          </span>
          {showTenantBadge && tenantSlug && (
            <span className="font-mono text-[10px] text-slate-500" aria-label={`Tenant ${tenantSlug}`}>
              {tenantSlug}
            </span>
          )}
          {hasConflict && (
            <span title={`${proposals.length} proposals in this cluster conflict; review in detail pane`}>
              <AlertTriangle className="h-3.5 w-3.5 text-amber-500" aria-label="Has conflict" />
            </span>
          )}
        </div>
        <Badge variant="outline" className="font-mono text-[10px]" aria-label={`${proposals.length} proposals`}>
          {proposals.length}
        </Badge>
        <ConfidenceDots
          value={cluster.cumulative_confidence_avg}
          showNumeric={false}
          showLabel={false}
          size="sm"
        />
        <div className="hidden items-center gap-0.5 sm:flex">
          {cluster.agent_slugs.slice(0, 4).map((slug) => (
            <AgentBadge key={slug} slug={slug} variant="compact" />
          ))}
          {cluster.agent_slugs.length > 4 && (
            <span className="text-xs text-muted-foreground">
              +{cluster.agent_slugs.length - 4}
            </span>
          )}
        </div>
        <span className="hidden text-xs text-muted-foreground md:inline">{created}</span>
      </header>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: prefersReduced ? 0 : 0.18, ease: "easeInOut" }}
            className="overflow-hidden border-t bg-card/40"
          >
            <div className="space-y-1 p-2">
              {visible.map((p) =>
                mobileSwipe ? (
                  <SwipeableProposalRow
                    key={p.proposal_id}
                    proposal={p}
                    focused={focusedProposalId === p.proposal_id}
                    selected={selectedProposalIds?.has(p.proposal_id) ?? false}
                    onSelect={() => onSelectProposal?.(p)}
                    onFocus={() => onFocusProposal?.(p)}
                    onApprove={() => onQuickApprove?.(p)}
                    onReject={() => onQuickReject?.(p)}
                  />
                ) : (
                  <ProposalRow
                    key={p.proposal_id}
                    proposal={p}
                    focused={focusedProposalId === p.proposal_id}
                    selected={selectedProposalIds?.has(p.proposal_id) ?? false}
                    onSelect={() => onSelectProposal?.(p)}
                    onFocus={() => onFocusProposal?.(p)}
                    onApprove={() => onQuickApprove?.(p)}
                    onReject={() => onQuickReject?.(p)}
                  />
                ),
              )}
              {overflow > 0 && (
                <p className="px-3 py-1 text-xs text-muted-foreground">
                  +{overflow} more in this cluster — open detail to review
                </p>
              )}
            </div>
            <footer className="flex items-center justify-end gap-2 border-t bg-muted/30 px-3 py-2">
              <Button
                size="sm"
                variant="outline"
                className="text-emerald-600 hover:bg-emerald-500/10"
                disabled={eligibleCount === 0}
                onClick={(e) => {
                  e.stopPropagation();
                  onApproveAll?.();
                }}
              >
                Approve all ≥ 0.85 ({eligibleCount})
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="text-rose-600 hover:bg-rose-500/10"
                onClick={(e) => {
                  e.stopPropagation();
                  onRejectAll?.();
                }}
              >
                Reject all
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenDetail?.();
                }}
              >
                Open detail →
              </Button>
            </footer>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  );
}
