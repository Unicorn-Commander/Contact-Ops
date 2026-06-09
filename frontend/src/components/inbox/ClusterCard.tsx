/**
 * ClusterCard - one card per entity (or per dedup cluster, per agent
 * batch, etc., depending on cluster_kind).
 *
 * Header: avatar, entity name, N proposals, cumulative confidence,
 *   row of compact AgentBadges, relative time,
 *   expand/collapse chevron.
 * Body (expanded): 1-N ProposalRows.
 * Footer: [Approve all >=0.85] [Reject all] [Open detail].
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
import { ProposalRow, type RowClickMeta } from "@/components/inbox/ProposalRow";
import { SwipeableProposalRow } from "@/components/inbox/SwipeableProposalRow";

const NO_MODS: RowClickMeta = { shiftKey: false, metaKey: false, ctrlKey: false };
import { AgentBadge, KeyboardHint, MonoNumeric } from "@/design-system";
import { ConfidenceMeterV2 } from "@/design-system/v2/ConfidenceMeterV2";

const MAX_INLINE = 12;

export type ClusterCardProps = {
  cluster: ProposalCluster;
  proposals: Proposal[];
  expanded: boolean;
  onToggle: () => void;
  focusedProposalId?: string | null;
  selectedProposalIds?: Set<string>;
  onSelectProposal?: (proposal: Proposal, meta: RowClickMeta) => void;
  /**
   * ⌘/Ctrl- or Shift-click on the cluster *header* selects the cluster's
   * proposals (instead of expanding). Lets a queue of singleton clusters be
   * bulk-triaged from the visible layer without expanding each one.
   */
  onClusterModifierSelect?: (meta: RowClickMeta) => void;
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
  onClusterModifierSelect,
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
  const clusterSelected =
    Boolean(selectedProposalIds) &&
    proposals.length > 0 &&
    proposals.every((p) => selectedProposalIds!.has(p.proposal_id));

  return (
    <Card
      className={cn(
        "co-v2-glass-calm overflow-hidden border-transparent",
        clusterSelected && "ring-1 ring-primary/60",
      )}
    >
      <header
        role="button"
        tabIndex={0}
        onClick={(e) => {
          if ((e.shiftKey || e.metaKey || e.ctrlKey) && onClusterModifierSelect) {
            e.preventDefault();
            onClusterModifierSelect({ shiftKey: e.shiftKey, metaKey: e.metaKey, ctrlKey: e.ctrlKey });
            return;
          }
          onToggle();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        className={cn(
          "flex items-center gap-co-8 px-co-12 py-co-8",
          "cursor-pointer select-none",
          "transition-colors duration-150 ease-snappy hover:bg-[oklch(var(--co-brand-500)/0.06)]",
          clusterSelected && "bg-primary/10",
          "focus-ring",
        )}
        aria-expanded={expanded}
        aria-label={`Cluster for ${cluster.entity_display_name}, ${proposals.length} proposals`}
        title={
          onClusterModifierSelect
            ? "Click to expand · ⌘-click to select · ⇧-click to select a range"
            : undefined
        }
      >
        <ChevronRight
          className={cn(
            "h-4 w-4 text-muted-foreground transition-transform",
            expanded && "rotate-90",
            prefersReduced && "transition-none",
          )}
          aria-hidden="true"
        />
        <Avatar className="h-7 w-7 rounded-[var(--radius-sm)]">
          {cluster.entity_avatar_url ? (
            <AvatarImage src={cluster.entity_avatar_url} alt={cluster.entity_display_name} />
          ) : null}
          <AvatarFallback className="rounded-[var(--radius-sm)] bg-muted font-mono text-11 text-muted-foreground">
            {initials(cluster.entity_display_name)}
          </AvatarFallback>
        </Avatar>
        <div className="flex min-w-0 flex-1 items-center gap-co-6">
          <span className="truncate text-13 font-semibold text-foreground" title={cluster.entity_display_name}>
            {cluster.entity_display_name.length > 40
              ? cluster.entity_display_name.slice(0, 40) + "..."
              : cluster.entity_display_name}
          </span>
          {showTenantBadge && tenantSlug && (
            <span className="font-mono text-11 text-muted-foreground" aria-label={`Tenant ${tenantSlug}`}>
              {tenantSlug}
            </span>
          )}
          {hasConflict && (
            <span title={`${proposals.length} proposals in this cluster conflict; review in detail pane`}>
              <AlertTriangle className="h-3.5 w-3.5 text-warning" aria-label="Has conflict" />
            </span>
          )}
        </div>
        <Badge variant="outline" className="h-5 rounded-[var(--radius-sm)] px-co-6 font-mono text-11" aria-label={`${proposals.length} proposals`}>
          <MonoNumeric tone="muted">{proposals.length}</MonoNumeric>
        </Badge>
        <ConfidenceMeterV2 value={cluster.cumulative_confidence_avg} showLabel={false} />
        <div className="hidden items-center gap-0.5 sm:flex">
          {cluster.agent_slugs.slice(0, 4).map((slug) => (
            <AgentBadge key={slug} slug={slug} size="xs" showLabel={false} />
          ))}
          {cluster.agent_slugs.length > 4 && (
            <span className="text-12 text-muted-foreground">
              +{cluster.agent_slugs.length - 4}
            </span>
          )}
        </div>
        <span className="hidden text-12 text-muted-foreground md:inline">{created}</span>
      </header>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: prefersReduced ? 0 : 0.18, ease: "easeInOut" }}
            className="overflow-hidden border-t border-[oklch(var(--co-v2-glass-hairline))] bg-background/30"
          >
            <div className="space-y-co-4 p-co-6">
              {visible.map((p) =>
                mobileSwipe ? (
                  <SwipeableProposalRow
                    key={p.proposal_id}
                    proposal={p}
                    focused={focusedProposalId === p.proposal_id}
                    selected={selectedProposalIds?.has(p.proposal_id) ?? false}
                    onSelect={() => onSelectProposal?.(p, NO_MODS)}
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
                    onSelect={(meta) => onSelectProposal?.(p, meta)}
                    onFocus={() => onFocusProposal?.(p)}
                    onApprove={() => onQuickApprove?.(p)}
                    onReject={() => onQuickReject?.(p)}
                  />
                ),
              )}
              {overflow > 0 && (
                <p className="px-co-8 py-co-4 text-12 text-muted-foreground">
                  +{overflow} more in this cluster. Open detail to review.
                </p>
              )}
            </div>
            <footer className="flex flex-wrap items-center justify-end gap-co-6 border-t border-[oklch(var(--co-v2-glass-hairline))] bg-muted/30 px-co-8 py-co-6">
              <Button
                size="sm"
                variant="outline"
                className="h-7 gap-co-4 border-success/40 bg-success/10 text-12 text-success hover:bg-success/20"
                disabled={eligibleCount === 0}
                onClick={(e) => {
                  e.stopPropagation();
                  onApproveAll?.();
                }}
              >
                Approve &gt;= 0.85 (<MonoNumeric tone="strong">{eligibleCount}</MonoNumeric>)
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-12 text-destructive hover:bg-destructive/10"
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
                className="h-7 gap-co-4 text-12"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenDetail?.();
                }}
              >
                Open detail
                <KeyboardHint keys="E" label="Expand focused cluster" />
              </Button>
            </footer>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  );
}
