/**
 * TieredApproveButton - single button, behavior switches by tier.
 *
 * Tier semantics (server is source of truth; this is local UX):
 *   T0  auto-apply (never reaches this button)
 *   T1  one-click: label "Approve · y"
 *   T2  reviewed: disabled until EvidencePanel scrolled >=50% into view
 *       (IntersectionObserver intersectionRatio); label "Read evidence
 *       to approve" -> "Approve · y"
 *   T3  diff-reviewed: disabled until every "decide" field has a
 *       choice in the StructuredDiff
 *   T4  typed-phrase: opens TypedPhraseConfirm modal
 *
 * Doesn't compute tier itself; consumes pre-computed `tier` from
 * selectTier(p, ctx).
 */
import { useEffect, useRef, useState } from "react";
import type { Proposal, Tier } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { TypedPhraseConfirm } from "@/components/inbox/TypedPhraseConfirm";
import { expectedPhraseFor } from "@/lib/inbox/tierSelector";
import { cn } from "@/lib/utils";
import { KeyboardHint, Spinner } from "@/design-system";

export type TieredApproveButtonProps = {
  proposal: Proposal;
  tier: Tier;
  /** For T3: true if every "decide" field is chosen. */
  diffReady?: boolean;
  busy?: boolean;
  /** Caller fires this with the resolved typed phrase (or empty for T<4). */
  onApprove: (typedPhrase: string | null) => void;
  /** For the T4 misclick path: lets the modal offer "Snooze instead". */
  onOfferSnooze?: () => void;
};

export function TieredApproveButton({
  proposal,
  tier,
  diffReady = true,
  busy = false,
  onApprove,
  onOfferSnooze,
}: TieredApproveButtonProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [evidenceSeen, setEvidenceSeen] = useState(tier !== 2);
  const observerRef = useRef<IntersectionObserver | null>(null);

  // T2: watch for the EvidencePanel to scroll into view.
  useEffect(() => {
    if (tier !== 2) {
      setEvidenceSeen(true);
      return;
    }
    setEvidenceSeen(false);
    const el = document.querySelector<HTMLElement>(
      `[data-evidence-panel][data-proposal-id="${proposal.proposal_id}"]`,
    );
    if (!el) return;
    observerRef.current?.disconnect();
    observerRef.current = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.intersectionRatio >= 0.5) {
            setEvidenceSeen(true);
            observerRef.current?.disconnect();
          }
        }
      },
      { threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
    observerRef.current.observe(el);
    return () => observerRef.current?.disconnect();
  }, [tier, proposal.proposal_id]);

  if (tier === 4) {
    const phrase = expectedPhraseFor(proposal);
    return (
      <>
        <Button
          className="bg-primary text-primary-foreground hover:bg-primary/90"
          disabled={busy}
          onClick={() => setConfirmOpen(true)}
        >
          {busy ? <Spinner size="sm" label="" /> : null}
          Type to approve
        </Button>
        {phrase && (
          <TypedPhraseConfirm
            open={confirmOpen}
            onOpenChange={setConfirmOpen}
            expectedPhrase={phrase}
            policyExplanation={policyExplanationFor(proposal)}
            policyDetail={policyDetailFor(proposal)}
            busy={busy}
            onConfirm={(typed) => {
              setConfirmOpen(false);
              onApprove(typed);
            }}
            onOfferSnooze={onOfferSnooze}
          />
        )}
      </>
    );
  }

  if (tier === 3) {
    return (
      <Button
        className={cn(
          "bg-primary text-primary-foreground hover:bg-primary/90",
          !diffReady && "cursor-not-allowed opacity-50",
        )}
        disabled={!diffReady || busy}
        onClick={() => onApprove(null)}
        title={
          diffReady
            ? "Approve · Y"
            : "Choose each field above to approve"
        }
      >
        {diffReady ? (
          <>
            {busy ? <Spinner size="sm" label="" /> : null}
            Approve
            <KeyboardHint
              keys="Y"
              label="Approve focused proposal"
              className="border-primary-foreground/30 bg-primary-foreground/10 text-primary-foreground"
            />
          </>
        ) : (
          "Choose each field to approve"
        )}
      </Button>
    );
  }

  if (tier === 2) {
    return (
      <Button
        className={cn(
          "bg-primary text-primary-foreground hover:bg-primary/90",
          !evidenceSeen && "cursor-not-allowed opacity-50",
        )}
        disabled={!evidenceSeen || busy}
        onClick={() => onApprove(null)}
        title={
          evidenceSeen
            ? "Approve · Y"
            : "Scroll the evidence panel into view first"
        }
      >
        {evidenceSeen ? (
          <>
            {busy ? <Spinner size="sm" label="" /> : null}
            Approve
            <KeyboardHint
              keys="Y"
              label="Approve focused proposal"
              className="border-primary-foreground/30 bg-primary-foreground/10 text-primary-foreground"
            />
          </>
        ) : (
          "Read evidence to approve"
        )}
      </Button>
    );
  }

  // T1
  return (
    <Button
      className="bg-primary text-primary-foreground hover:bg-primary/90"
      disabled={busy}
      onClick={() => onApprove(null)}
    >
      {busy ? <Spinner size="sm" label="" /> : null}
      Approve
      <KeyboardHint
        keys="Y"
        label="Approve focused proposal"
        className="border-primary-foreground/30 bg-primary-foreground/10 text-primary-foreground"
      />
    </Button>
  );
}

function policyExplanationFor(p: Proposal): string {
  if (p.compliance.hipaa) return "This proposal touches HIPAA-tagged data.";
  if (p.is_delete) return `Deleting ${p.entity_kind} "${p.entity_display_name}" is irreversible.`;
  if (p.cross_tenant) return "This is a cross-tenant proposal.";
  if (p.bulk_count > 10) return `This will apply to ${p.bulk_count} entities at once.`;
  if (p.touches_case_file) return "This proposal affects an active case file in Project-Ops.";
  return "This proposal requires typed confirmation.";
}

function policyDetailFor(p: Proposal): string | undefined {
  if (p.touches_case_file && p.case_file_links.length > 0) {
    const cases = p.case_file_links.map((l) => l.project_name).join(", ");
    return `Case files: ${cases}. Tier 4 forced by case-file policy.`;
  }
  if (p.compliance.hipaa) {
    return "HIPAA-tagged tenant: every mutation requires typed confirmation regardless of confidence.";
  }
  return undefined;
}
