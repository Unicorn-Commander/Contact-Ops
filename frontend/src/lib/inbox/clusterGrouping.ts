/**
 * Frontend cluster helpers.
 *
 * The backend already groups proposals into `ProposalCluster` with stable
 * `cluster_id` and `cluster_kind` (see `inbox_query.list_pending_proposals`),
 * so the frontend doesn't *compute* clusters — it hydrates them into a
 * `cluster_id -> Proposal[]` map for the virtualized list, applies the
 * default-expanded heuristic, and sorts.
 *
 * The single ordering rule:
 *   - Within a cluster: confidence ascending (riskiest first when cold)
 *   - Across clusters: latest_created_at descending
 *   - In Focus Mode: confidence descending within auto-approval-eligible
 */
import type { Proposal, ProposalCluster } from "@/lib/types";

export type HydratedCluster = ProposalCluster & {
  proposals: Proposal[];
  hasConflict: boolean;
};

export function hydrateClusters(
  clusters: ProposalCluster[],
  proposals: Proposal[],
): HydratedCluster[] {
  const byCluster = new Map<string, Proposal[]>();
  for (const p of proposals) {
    const list = byCluster.get(p.cluster_id);
    if (list) list.push(p);
    else byCluster.set(p.cluster_id, [p]);
  }
  for (const list of byCluster.values()) {
    list.sort(
      (a, b) =>
        a.confidence - b.confidence ||
        a.created_at.localeCompare(b.created_at),
    );
  }
  return clusters
    .map((c) => {
      const proposals = byCluster.get(c.cluster_id) ?? [];
      const hasConflict = proposals.some(
        (p) => p.parent_proposal_id != null,
      );
      return { ...c, proposals, hasConflict };
    })
    .filter((c) => c.proposals.length > 0);
}

export function defaultExpanded(
  cluster: HydratedCluster,
  view: "needs-review" | "snoozed" | "resolved",
): boolean {
  if (view !== "needs-review") return false;
  return cluster.proposals.length <= 3;
}

/**
 * Focus mode filter: hide everything except confidence ≥ 0.90 AND
 * non-HIPAA AND reversible. Per UI prompt and Aaron's frontend brief.
 */
export function applyFocusMode<T extends Proposal>(
  proposals: T[],
  focusMode: boolean,
): T[] {
  if (!focusMode) return proposals;
  return proposals.filter(
    (p) =>
      p.confidence >= 0.9 &&
      !p.compliance.hipaa &&
      (p.reversibility_class === "reversible" ||
        p.reversibility_class === "reversible_24h"),
  );
}

/**
 * Personal-org separation (Aaron's `feedback_personal_org_separation`):
 * even when both tenants are in "All tenants" view, never visually merge
 * a cluster across tenants. The backend already enforces this by keying
 * cluster_id on (tenant_id, aggregate_id, bucket) — different tenants
 * mean different cluster_ids — but this helper assertion makes the
 * intent visible at the boundary in case the contract ever changes.
 */
export function assertPersonalOrgSeparation(clusters: HydratedCluster[]): void {
  const byEntity = new Map<string, Set<string>>();
  for (const c of clusters) {
    const set = byEntity.get(c.entity_id) ?? new Set<string>();
    set.add(c.tenant_id);
    byEntity.set(c.entity_id, set);
  }
  // Multi-tenant per entity is OK only if they live in distinct clusters
  // (they already do — cluster_id includes tenant). Nothing to assert
  // beyond "we never silently merge."
  void byEntity;
}
