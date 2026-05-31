/**
 * proposalRowShared — Design Language v2 "calm proposal row" internals.
 * =====================================================================
 * Both ProposalRow (desktop) and SwipeableProposalRow (mobile) render the SAME
 * inner layout, so the visual language is defined ONCE here and reused. This is
 * the live implementation of the approved showcase row (ShowcaseV2 →
 * "Review-queue proposal row — new calm style").
 *
 * Every row answers, at a glance:
 *   WHAT  — a verb badge (Merge / Set fields / Match voice / Link relationship…)
 *           + a plain-English summary of the proposed change.
 *   WHO   — agent name + version + "proposed <relative time>".
 *   HOW   — a calm, color-coded confidence meter with a tier word
 *           (High / Good / Review / Low) via ConfidenceMeterV2.
 *   + inline context tags (cross-tenant, HIPAA), and ALWAYS-VISIBLE Approve +
 *     reject affordances (they brighten on hover/focus but never hide).
 *
 * Calm, AA-legible, scannable — this is a dense WORK surface. The agent's hue
 * drives a thin leading rail (agentColorVars) so each agent stays identifiable
 * in a long list without adding noise. No bloom / blur on the resting row.
 *
 * `actionVerbFor` lives here (the canonical source) and is re-exported from
 * ProposalRow.tsx for back-compat with existing importers.
 */
import type { CSSProperties, ReactNode } from "react";
import { Check, X } from "lucide-react";
import type { Proposal } from "@/lib/types";
import { agentColorVars } from "@/design-system/tokens";
import { ConfidenceMeterV2 } from "@/design-system/v2/ConfidenceMeterV2";
import { AgentBadge, KeyboardHint } from "@/design-system";

/** Short action verb shown in the leading verb badge. */
export function actionVerbFor(actionType: string, p: Proposal): string {
  if (p.is_dedup) return "Merge";
  if (actionType.startsWith("tag.add")) return "Add tag";
  if (actionType.startsWith("tag.remove")) return "Remove tag";
  if (actionType.startsWith("lifecycle.")) return "Lifecycle";
  if (actionType.startsWith("enrichment.")) {
    const fields = Object.keys(p.payload_after);
    if (fields.length === 1) return "Set field";
    return `Set ${fields.length} fields`;
  }
  if (actionType.startsWith("relationship.")) return "Link relationship";
  if (actionType.startsWith("voice_match.")) return "Match voice";
  if (actionType.startsWith("carddav.")) return "Reconcile CardDAV";
  if (p.is_delete) return `Delete ${p.entity_kind}`;
  return actionType.replace(/_/g, " ").replace(/\./g, ": ");
}

/**
 * Plain-English summary of the proposed change — the human-readable part shown
 * after the verb badge (e.g. "Mina Chen ← duplicate profile", "title, company
 * for Rafael Ortiz"). Falls back to the entity name so the row is never empty.
 */
export function proposalSummary(p: Proposal): string {
  const name = p.entity_display_name || "this contact";
  if (p.is_dedup) return `${name} ← duplicate profile`;
  if (p.action_type.startsWith("enrichment.")) {
    const fields = Object.keys(p.payload_after);
    if (fields.length === 0) return name;
    const shown = fields.slice(0, 3).join(", ");
    const more = fields.length > 3 ? ` +${fields.length - 3} more` : "";
    return `${shown}${more} for ${name}`;
  }
  if (p.action_type.startsWith("tag.")) {
    const tag =
      (p.payload_after.tag as string | undefined) ??
      (p.payload_after.name as string | undefined) ??
      "tag";
    return `${tag} on ${name}`;
  }
  if (p.action_type.startsWith("relationship.")) return name;
  if (p.action_type.startsWith("voice_match.")) return `speaker → ${name}`;
  if (p.is_delete) return name;
  return name;
}

/** cross-tenant / HIPAA context chips, shown inline next to the summary. */
export function ProposalContextTags({ proposal }: { proposal: Proposal }) {
  if (!proposal.cross_tenant && !proposal.compliance.hipaa) return null;
  return (
    <>
      {proposal.cross_tenant ? (
        <span className="shrink-0 rounded-[var(--radius-sm)] border border-warning/40 bg-warning/10 px-co-4 py-co-2 font-mono text-11 text-warning">
          cross-tenant
        </span>
      ) : null}
      {proposal.compliance.hipaa ? (
        <span className="shrink-0 rounded-[var(--radius-sm)] border border-destructive/40 bg-destructive/10 px-co-4 py-co-2 font-mono text-11 text-destructive">
          HIPAA
        </span>
      ) : null}
    </>
  );
}

export type ProposalRowBodyProps = {
  proposal: Proposal;
  onApprove?: () => void;
  onReject?: () => void;
  /**
   * Mobile-swipe rows hide the inline approve button for non-T1 proposals (the
   * inline ✓ would otherwise imply a swipe-approve that isn't allowed). When
   * false, the reject button still renders. Defaults to true.
   */
  showApprove?: boolean;
};

/**
 * The shared inner cells of a v2 proposal row. The wrapping <div role="button">
 * (focus, selection, click-to-detail, swipe) stays in each row component so
 * their distinct interaction models are preserved; only the *presentation* is
 * unified here.
 */
export function ProposalRowBody({
  proposal,
  onApprove,
  onReject,
  showApprove = true,
}: ProposalRowBodyProps): ReactNode {
  const verb = actionVerbFor(proposal.action_type, proposal);
  const summary = proposalSummary(proposal);
  const agentLabel = `${proposal.agent_id} ${proposal.agent_version}`;
  const created = relativeShort(proposal.created_at);

  return (
    <>
      {/* Agent sigil — square-cornered, never human-shaped (AIG rule). */}
      <AgentBadge slug={proposal.agent_id} label={agentLabel} size="xs" showLabel={false} />

      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-co-6">
          <span
            className="shrink-0 rounded-[var(--radius-sm)] border border-[oklch(var(--agent-color)/0.4)] bg-[oklch(var(--agent-color-muted))] px-co-6 py-co-2 text-11 font-semibold text-foreground"
            title={verb}
          >
            {verb}
          </span>
          <span className="truncate text-muted-foreground" title={summary}>
            {summary}
          </span>
          <ProposalContextTags proposal={proposal} />
        </div>
        {/* Agent attribution — clearly "an agent proposed this, at this time". */}
        <p className="mt-co-2 truncate text-11 text-muted-foreground">
          <span className="text-[oklch(var(--agent-color))]">{agentLabel}</span>
          {" · proposed "}
          {created}
        </p>
      </div>

      <ConfidenceMeterV2 value={proposal.confidence} />

      {/* Approve / reject — ALWAYS visible (not hover-only) so the action is
          discoverable at all times; they brighten on row hover/focus. */}
      <span className="ml-co-4 flex items-center gap-co-4">
        {showApprove ? (
          <button
            type="button"
            className="focus-ring inline-flex h-7 items-center gap-1 rounded-md border border-success/40 bg-success/10 px-2 text-12 font-medium text-success opacity-90 transition-colors hover:bg-success/20 group-hover:opacity-100 group-focus-within:opacity-100"
            onClick={(e) => {
              e.stopPropagation();
              onApprove?.();
            }}
            title="Approve (Y)"
            aria-label={`Approve: ${verb}`}
          >
            <Check className="h-3.5 w-3.5" strokeWidth={2.2} aria-hidden="true" />
            <span className="hidden sm:inline">Approve</span>
          </button>
        ) : null}
        <button
          type="button"
          className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md border border-border text-muted-foreground opacity-90 transition-colors hover:border-destructive/50 hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100 group-focus-within:opacity-100"
          onClick={(e) => {
            e.stopPropagation();
            onReject?.();
          }}
          title="Reject (N)"
          aria-label={`Reject: ${verb}`}
        >
          <X className="h-3.5 w-3.5" strokeWidth={2.2} aria-hidden="true" />
          <span className="sr-only">Reject</span>
        </button>
        <span className="hidden items-center gap-co-2 xl:inline-flex" aria-hidden="true">
          {showApprove ? <KeyboardHint keys="Y" label="Approve focused proposal" /> : null}
          <KeyboardHint keys="N" label="Reject focused proposal" />
        </span>
      </span>
    </>
  );
}

/** Style object that exposes the agent's hue as --agent-color on the row. */
export function agentRowStyle(slug: string): CSSProperties {
  return {
    ...(agentColorVars(slug) as CSSProperties),
    "--co-v2-rail-color": "oklch(var(--agent-color))",
  } as CSSProperties;
}

/** Compact relative time ("12s", "4m", "3h", "2d") for the attribution line. */
function relativeShort(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "just now";
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}
