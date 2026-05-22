/**
 * EmptyStateInboxZero — NOT celebration. Per Aaron's feedback_no_pitch_tone.
 *
 * Renders a stats summary that helps Aaron understand fleet health,
 * plus three action buttons. No emojis, no exclamation marks, no
 * "Welcome!", no "Great job!". The substrate is functional, not
 * affirmational.
 *
 * Three variants:
 *   "cold-start": no resolved decisions yet -> softer copy
 *   "summary":   resolved decisions exist -> stats summary
 *   "loading":   stats still resolving
 */
import { Button } from "@/components/ui/button";

export type EmptyStateStats = {
  today: number;
  approved: number;
  rejected: number;
  snoozed: number;
  topAgentToday?: string | null;
  topAgentCount?: number;
  bestCalibratedAgent?: string | null;
  bestCalibratedRate?: number | null;
  nextBatchEtaMinutes?: number | null;
};

export type EmptyStateInboxZeroProps = {
  stats?: EmptyStateStats;
  onReviewSnoozed?: () => void;
  onOpenAgentReport?: () => void;
  onOpenAutoApproveSettings?: () => void;
  /** Force "cold-start" copy when the fleet hasn't produced anything yet. */
  coldStart?: boolean;
};

export function EmptyStateInboxZero({
  stats,
  onReviewSnoozed,
  onOpenAgentReport,
  onOpenAutoApproveSettings,
  coldStart = false,
}: EmptyStateInboxZeroProps) {
  if (coldStart) {
    return (
      <section className="mx-auto max-w-md py-12 text-center text-sm text-muted-foreground">
        <h2 className="mb-2 text-base font-semibold text-foreground">No proposals yet.</h2>
        <p>
          Agents start producing within the first 6 hours. Come back this evening.
        </p>
      </section>
    );
  }

  if (!stats) {
    return (
      <section className="mx-auto max-w-md py-12 text-center text-sm text-muted-foreground">
        <h2 className="mb-2 text-base font-semibold text-foreground">Inbox is clear.</h2>
        <p>Stats loading…</p>
      </section>
    );
  }

  const bestRate =
    stats.bestCalibratedRate != null
      ? `${Math.round(stats.bestCalibratedRate * 100)}%`
      : null;

  return (
    <section className="mx-auto max-w-xl space-y-6 py-12">
      <header className="space-y-1 text-center">
        <h2 className="text-base font-semibold text-foreground">Inbox is clear.</h2>
        <p className="font-mono text-sm tabular-nums text-muted-foreground">
          {stats.today} today · {stats.approved} approved · {stats.rejected} rejected ·{" "}
          {stats.snoozed} snoozed
        </p>
      </header>

      <div className="space-y-1 rounded-md border bg-card p-4 text-sm">
        {stats.topAgentToday && (
          <p>
            <span className="text-muted-foreground">Top agent today:</span>{" "}
            <span className="font-medium">{stats.topAgentToday}</span>
            {stats.topAgentCount != null && (
              <span className="text-muted-foreground"> ({stats.topAgentCount} proposals)</span>
            )}
          </p>
        )}
        {stats.bestCalibratedAgent && bestRate && (
          <p>
            <span className="text-muted-foreground">Best calibrated this week:</span>{" "}
            <span className="font-medium">{stats.bestCalibratedAgent}</span>{" "}
            <span className="text-muted-foreground">
              ({bestRate} approval at conf ≥ 0.90)
            </span>
          </p>
        )}
        {stats.nextBatchEtaMinutes != null ? (
          <p>
            <span className="text-muted-foreground">Next batch arriving in</span>{" "}
            ~{stats.nextBatchEtaMinutes} min
          </p>
        ) : (
          <p className="text-muted-foreground">Next batch: scheduled</p>
        )}
      </div>

      <div className="flex flex-wrap justify-center gap-2">
        <Button variant="outline" onClick={onReviewSnoozed}>
          Review snoozed
        </Button>
        <Button variant="outline" onClick={onOpenAgentReport}>
          Agent feedback report
        </Button>
        <Button variant="outline" onClick={onOpenAutoApproveSettings}>
          Adjust auto-approve thresholds
        </Button>
      </div>
    </section>
  );
}
