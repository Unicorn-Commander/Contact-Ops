/**
 * EmptyStateInboxZero - NOT celebration. Per Aaron's feedback_no_pitch_tone.
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
import { AgentBadge, MonoNumeric } from "@/design-system";

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
      <section className="mx-auto max-w-md rounded-md border border-border bg-card px-co-24 py-co-32 text-center text-13 text-muted-foreground shadow-[var(--shadow-1)]">
        <h2 className="mb-co-8 text-18 font-semibold text-foreground">No proposals yet.</h2>
        <p className="leading-6">
          Agents start producing within the first 6 hours. Come back this evening.
        </p>
      </section>
    );
  }

  if (!stats) {
    return (
      <section className="mx-auto max-w-md rounded-md border border-border bg-card px-co-24 py-co-32 text-center text-13 text-muted-foreground shadow-[var(--shadow-1)]">
        <h2 className="mb-co-8 text-18 font-semibold text-foreground">Review is clear.</h2>
        <p>Stats loading...</p>
      </section>
    );
  }

  const bestRate =
    stats.bestCalibratedRate != null
      ? `${Math.round(stats.bestCalibratedRate * 100)}%`
      : null;

  return (
    <section className="mx-auto max-w-xl space-y-co-20 py-co-32">
      <header className="space-y-co-6 text-center">
        <h2 className="text-18 font-semibold text-foreground">Inbox is clear.</h2>
        <p className="text-13 text-muted-foreground">
          <MonoNumeric tone="muted">{stats.today}</MonoNumeric> today /{" "}
          <MonoNumeric tone="muted">{stats.approved}</MonoNumeric> approved /{" "}
          <MonoNumeric tone="muted">{stats.rejected}</MonoNumeric> rejected /{" "}
          <MonoNumeric tone="muted">{stats.snoozed}</MonoNumeric> snoozed
        </p>
      </header>

      <div className="space-y-co-8 rounded-md border border-border bg-card p-co-16 text-13 shadow-[var(--shadow-1)]">
        {stats.topAgentToday && (
          <p className="flex flex-wrap items-center gap-co-6">
            <span className="text-muted-foreground">Top agent today:</span>{" "}
            <AgentBadge slug={stats.topAgentToday} size="xs" />
            {stats.topAgentCount != null && (
              <span className="text-muted-foreground">
                (<MonoNumeric tone="muted">{stats.topAgentCount}</MonoNumeric> proposals)
              </span>
            )}
          </p>
        )}
        {stats.bestCalibratedAgent && bestRate && (
          <p className="flex flex-wrap items-center gap-co-6">
            <span className="text-muted-foreground">Best calibrated this week:</span>{" "}
            <AgentBadge slug={stats.bestCalibratedAgent} size="xs" />
            <span className="text-muted-foreground">
              ({bestRate} approval at conf &gt;= 0.90)
            </span>
          </p>
        )}
        {stats.nextBatchEtaMinutes != null ? (
          <p>
            <span className="text-muted-foreground">Next batch arriving in</span>{" "}
            ~<MonoNumeric>{stats.nextBatchEtaMinutes}</MonoNumeric> min
          </p>
        ) : (
          <p className="text-muted-foreground">Next batch: scheduled</p>
        )}
      </div>

      <div className="flex flex-wrap justify-center gap-co-8">
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
