/**
 * Friction-ladder tier computation. Mirror of the server-side
 * `_compute_tier` in `backend/contact_ops/services/inbox_mutations.py`.
 *
 * The client computes the tier locally for instant UI feedback (so we
 * can render "Approve" vs "Read evidence to approve" etc. without a
 * round-trip). The server re-validates on every approve call; if the
 * two disagree, the mutation returns STALE_TIER_POLICY and the UI
 * refetches.
 *
 * Algorithm (UI prompt §11.6 + Aaron's frontend brief):
 *   T4: HIPAA, delete, cross-tenant, bulk>10, touches a case file
 *   T3: dedup, ≥3 fields changed, relationship edge
 *   T2: confidence < 0.85, exposure=medium, irreversible/soft_delete
 *   T1: confidence < 0.97, exposure ∈ {none, low}, reversible
 *   T0: tenant-auto-approve enabled AND conf ≥ 0.97 AND reversible AND
 *       compliance=none — handled server-side, never returned for review
 *
 * Keep these branches in lock-step with the backend; any deviation
 * silently masks tier disagreements.
 */
import type { Proposal, Tier } from "@/lib/types";

export type TierContext = {
  /** Map of agentId -> auto-approve toggle. Driven by /settings tab. */
  tenantAutoApprove: Record<string, boolean>;
};

export function selectTier(p: Proposal, ctx: TierContext): Tier {
  if (
    p.compliance.hipaa ||
    p.touches_case_file ||
    p.cross_tenant ||
    p.is_delete ||
    p.bulk_count > 10
  ) {
    return 4;
  }
  if (p.is_dedup || p.fields_changed >= 3 || p.is_edge) {
    return 3;
  }
  if (
    p.confidence < 0.85 ||
    p.compliance.exposure === "medium" ||
    p.reversibility_class === "irreversible" ||
    p.reversibility_class === "soft_delete"
  ) {
    return 2;
  }
  if (
    p.confidence < 0.97 ||
    (p.compliance.exposure !== "none" && p.compliance.exposure !== "low")
  ) {
    return 1;
  }
  if (ctx.tenantAutoApprove[p.agent_id]) {
    return 0;
  }
  return 1;
}

/**
 * The phrase the user must type to approve a Tier-4 proposal. None means
 * tier ≤ 3 (no typed phrase required).
 *
 * Server re-derives + re-validates the same phrase. Backend grammar is
 * case-insensitive + whitespace-trimmed (per Aaron's B3).
 */
export function expectedPhraseFor(
  p: Proposal,
  opts?: { sourceTenantSlug?: string; targetTenantSlug?: string },
): string | null {
  if (p.is_delete && p.entity_display_name) return p.entity_display_name;
  if (p.compliance.hipaa) return "approve hipaa";
  if (p.bulk_count > 10) return `approve ${p.bulk_count} items`;
  if (p.cross_tenant && opts?.sourceTenantSlug && opts?.targetTenantSlug) {
    return `${opts.sourceTenantSlug} to ${opts.targetTenantSlug}`;
  }
  return null;
}

/**
 * Case-insensitive + whitespace-trimmed match (mirrors server B3).
 * Inner whitespace is NOT collapsed — "approve  hipaa" ≠ "approve hipaa".
 */
export function phraseMatches(
  supplied: string | null | undefined,
  expected: string | null,
): boolean {
  if (expected === null) return true;
  if (supplied === null || supplied === undefined) return false;
  return supplied.trim().toLowerCase() === expected.trim().toLowerCase();
}
