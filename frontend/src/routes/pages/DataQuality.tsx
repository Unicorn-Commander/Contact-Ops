/**
 * /data-quality — find and fix the dirty data a real import leaves behind.
 *
 * Calm DATA surface (the refined-hybrid direction): a quiet header, a metric
 * strip, and one card per issue class. The two safe fixes (clean org names,
 * canonicalize tags) are unambiguous, audited, dry-run-by-default operations;
 * the page shows the exact before -> after, then a two-step confirm before any
 * write. Duplicate organizations are surfaced read-only here — merging them is
 * a separate, per-group human decision in the merge cockpit.
 */
import { useEffect, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Building2,
  Check,
  Hash,
  Quote,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Tags as TagsIcon,
  Users,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { MonoNumeric, Spinner } from "@/design-system";
import {
  useCanonicalizeTagsMutation,
  useCleanOrgNamesMutation,
  useDataQualityReport,
  useMergeOrganizationsMutation,
  useMergePeopleMutation,
} from "@/hooks/useDataQuality";
import type { DataQualityReport, DqDuplicateGroup, DqPersonDuplicateGroup } from "@/lib/types";
import { cn } from "@/lib/utils";

export function DataQualityRoute() {
  const report = useDataQualityReport();

  return (
    <div className="mx-auto w-full max-w-5xl space-y-co-16 px-co-4 py-co-8">
      <Header onRefresh={() => report.refetch()} refreshing={report.isFetching} />

      {report.isPending ? (
        <div className="flex items-center gap-co-8 py-co-32 text-13 text-muted-foreground">
          <Spinner size="sm" label="" />
          <span>Scanning your organizations and tags…</span>
        </div>
      ) : report.isError ? (
        <Card className="border-destructive/30 bg-destructive/5 p-co-16 text-13 text-destructive">
          Couldn't load the data-quality report. {(report.error as Error)?.message}
        </Card>
      ) : report.data ? (
        <ReportBody data={report.data} />
      ) : null}
    </div>
  );
}

function Header({ onRefresh, refreshing }: { onRefresh: () => void; refreshing: boolean }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-co-8">
      <div className="flex items-center gap-co-12">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[oklch(var(--co-brand-300)/0.3)] bg-[oklch(var(--co-brand-500)/0.12)] text-[oklch(var(--co-brand-300))]">
          <Sparkles className="h-5 w-5" strokeWidth={1.8} aria-hidden="true" />
        </span>
        <div>
          <h1 className="text-20 font-semibold tracking-tight text-foreground">Data Quality</h1>
          <p className="text-13 text-muted-foreground">
            Find and fix the dirty data left behind by imports — safely and reversibly.
          </p>
        </div>
      </div>
      <Button variant="ghost" size="sm" onClick={onRefresh} disabled={refreshing} className="gap-co-6">
        <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
        Rescan
      </Button>
    </header>
  );
}

function ReportBody({ data }: { data: DataQualityReport }) {
  const s = data.summary;
  const totalIssues =
    s.quote_wrapped +
    s.whitespace +
    s.phone_as_name +
    s.duplicate_groups +
    s.tag_variant_groups +
    (s.person_duplicate_groups ?? 0);

  return (
    <div className="space-y-co-16">
      {/* metric strip */}
      <div className="grid grid-cols-2 gap-co-8 sm:grid-cols-3 lg:grid-cols-6">
        <Metric icon={Quote} label="Quote-wrapped" value={s.quote_wrapped} tone="amber" />
        <Metric icon={Building2} label="Duplicate orgs" value={s.duplicate_orgs} tone="amber" />
        <Metric icon={Users} label="Duplicate people" value={s.duplicate_people ?? 0} tone="amber" />
        <Metric icon={TagsIcon} label="Tag variants" value={s.tag_variant_groups} tone="amber" />
        <Metric icon={Hash} label="Phone-as-name" value={s.phone_as_name} tone="rose" />
        <Metric icon={ShieldCheck} label="Scanned orgs" value={data.org_total} tone="neutral" />
      </div>

      {totalIssues === 0 ? (
        <Card className="flex items-center gap-co-12 border-success/30 bg-success/5 p-co-16">
          <ShieldCheck className="h-6 w-6 text-success" />
          <div>
            <p className="text-14 font-semibold text-foreground">Everything looks clean</p>
            <p className="text-12 text-muted-foreground">
              No quote-wrapped names, duplicate candidates, or tag variants found.
            </p>
          </div>
        </Card>
      ) : null}

      <OrgNamesSection data={data} />
      <TagsSection data={data} />
      <DuplicateOrgsSection data={data} />
      <DuplicatePeopleSection data={data} />
      <JobTitleOrgSection data={data} />
      <PhoneNameSection data={data} />
    </div>
  );
}

const TONE: Record<string, string> = {
  amber: "text-[oklch(var(--co-amber-500))]",
  rose: "text-[oklch(var(--co-rose-500))]",
  neutral: "text-muted-foreground",
};

function Metric({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: typeof Quote;
  label: string;
  value: number;
  tone: "amber" | "rose" | "neutral";
}) {
  const muted = value === 0;
  return (
    <Card className="flex items-center gap-co-8 p-co-8">
      <Icon
        className={cn("h-4 w-4 shrink-0", muted ? "text-muted-foreground/50" : TONE[tone])}
        strokeWidth={1.8}
      />
      <div className="min-w-0">
        <div className={cn("text-18 font-semibold tabular-nums", muted && "text-muted-foreground")}>
          <MonoNumeric tone={muted ? "muted" : "strong"}>{value}</MonoNumeric>
        </div>
        <div className="truncate text-11 text-muted-foreground">{label}</div>
      </div>
    </Card>
  );
}

function SectionCard({
  icon: Icon,
  title,
  subtitle,
  action,
  children,
  clean,
}: {
  icon: typeof Quote;
  title: string;
  subtitle: string;
  action?: ReactNode;
  children?: ReactNode;
  clean?: boolean;
}) {
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-co-8 border-b border-border bg-card/60 px-co-16 py-co-8">
        <div className="flex items-center gap-co-8">
          <span
            className={cn(
              "flex h-8 w-8 items-center justify-center rounded-lg",
              clean
                ? "bg-success/10 text-success"
                : "bg-[oklch(var(--co-brand-500)/0.1)] text-[oklch(var(--co-brand-300))]",
            )}
          >
            {clean ? <Check className="h-4 w-4" /> : <Icon className="h-4 w-4" strokeWidth={1.8} />}
          </span>
          <div>
            <h2 className="text-14 font-semibold text-foreground">{title}</h2>
            <p className="text-12 text-muted-foreground">{subtitle}</p>
          </div>
        </div>
        {action}
      </div>
      {children ? <div className="p-co-16">{children}</div> : null}
    </Card>
  );
}

/**
 * Two-step confirm: first click arms, a second click within 4s applies. Avoids
 * a modal dependency while still preventing an accidental bulk write.
 */
function ApplyButton({
  idleLabel,
  confirmLabel,
  busy,
  disabled,
  onApply,
}: {
  idleLabel: string;
  confirmLabel: string;
  busy: boolean;
  disabled?: boolean;
  onApply: () => void;
}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(t);
  }, [armed]);

  if (busy) {
    return (
      <Button size="sm" disabled className="gap-co-6">
        <Spinner size="sm" label="" />
        Applying…
      </Button>
    );
  }
  if (!armed) {
    return (
      <Button size="sm" disabled={disabled} onClick={() => setArmed(true)} className="gap-co-6">
        {idleLabel}
      </Button>
    );
  }
  return (
    <span className="inline-flex items-center gap-co-6">
      <Button
        size="sm"
        className="gap-co-4 bg-success text-success-foreground hover:bg-success/90"
        onClick={() => {
          setArmed(false);
          onApply();
        }}
      >
        <Check className="h-3.5 w-3.5" />
        {confirmLabel}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setArmed(false)}>
        Cancel
      </Button>
    </span>
  );
}

function NameFix({ before, after }: { before: string; after: string }) {
  return (
    <div className="flex min-w-0 items-center gap-co-8 text-13">
      <span className="truncate font-mono text-muted-foreground line-through decoration-destructive/40" title={before}>
        {before}
      </span>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="truncate font-medium text-foreground" title={after}>
        {after}
      </span>
    </div>
  );
}

function OrgNamesSection({ data }: { data: DataQualityReport }) {
  const clean = useCleanOrgNamesMutation();
  const fixes = [...data.issues.quote_wrapped, ...data.issues.whitespace];
  const count = data.summary.quote_wrapped + data.summary.whitespace;

  if (count === 0) {
    return (
      <SectionCard
        icon={Quote}
        title="Organization names"
        subtitle="No quote-wrapped or whitespace issues."
        clean
      />
    );
  }

  return (
    <SectionCard
      icon={Quote}
      title="Organization names"
      subtitle={`${count} name${count === 1 ? "" : "s"} have literal quotes or stray whitespace`}
      action={
        <ApplyButton
          idleLabel={`Clean ${count} name${count === 1 ? "" : "s"}`}
          confirmLabel={`Apply to ${count} org${count === 1 ? "" : "s"}`}
          busy={clean.isPending}
          onApply={() => clean.mutate({ dryRun: false })}
        />
      }
    >
      <ul className="divide-y divide-border/60">
        {fixes.slice(0, 12).map((o) => (
          <li key={o.org_id} className="py-co-6 first:pt-0 last:pb-0">
            <NameFix before={o.name} after={o.cleaned ?? o.name} />
          </li>
        ))}
      </ul>
      {fixes.length > 12 ? (
        <p className="pt-co-8 text-12 text-muted-foreground">
          + <MonoNumeric tone="muted">{fixes.length - 12}</MonoNumeric> more
        </p>
      ) : null}
    </SectionCard>
  );
}

function TagsSection({ data }: { data: DataQualityReport }) {
  const canon = useCanonicalizeTagsMutation();
  const groups = data.tag_variant_groups;

  if (groups.length === 0) {
    return (
      <SectionCard icon={TagsIcon} title="Tags" subtitle="No case or format variants." clean />
    );
  }

  return (
    <SectionCard
      icon={TagsIcon}
      title="Tags"
      subtitle={`${groups.length} set${groups.length === 1 ? "" : "s"} of duplicate tags differ only by case or format`}
      action={
        <ApplyButton
          idleLabel="Canonicalize tags"
          confirmLabel="Merge variants"
          busy={canon.isPending}
          onApply={() => canon.mutate({ dryRun: false })}
        />
      }
    >
      <ul className="space-y-co-8">
        {groups.map((g) => (
          <li key={g.canonical} className="flex flex-wrap items-center gap-co-6 text-13">
            {g.variants.map((v) => (
              <span
                key={v.tag}
                className="inline-flex items-center gap-co-4 rounded-[var(--radius-sm)] border border-border bg-muted/40 px-co-6 py-0.5 font-mono text-12 text-muted-foreground"
              >
                {v.tag}
                <span className="text-10 text-muted-foreground/70">{v.person_count}</span>
              </span>
            ))}
            <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
            <Badge variant="outline" className="rounded-[var(--radius-sm)] border-success/40 bg-success/10 font-mono text-12 text-success">
              {g.canonical}
            </Badge>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function DuplicateOrgsSection({ data }: { data: DataQualityReport }) {
  const groups = data.org_duplicate_groups;
  if (groups.length === 0) {
    return (
      <SectionCard
        icon={Building2}
        title="Duplicate organizations"
        subtitle="No duplicate candidates found."
        clean
      />
    );
  }
  return (
    <SectionCard
      icon={Building2}
      title="Duplicate organizations"
      subtitle={`${data.summary.duplicate_groups} group${data.summary.duplicate_groups === 1 ? "" : "s"} (${data.summary.duplicate_orgs} orgs) look like the same company`}
      action={
        <Badge variant="outline" className="rounded-[var(--radius-sm)] text-12 text-muted-foreground">
          Pick the survivor, then merge
        </Badge>
      }
    >
      <ul className="space-y-co-8">
        {groups.slice(0, 12).map((g) => (
          <DuplicateGroupRow key={g.key} group={g} />
        ))}
      </ul>
      {groups.length > 12 ? (
        <p className="pt-co-8 text-12 text-muted-foreground">
          + <MonoNumeric tone="muted">{groups.length - 12}</MonoNumeric> more groups
        </p>
      ) : null}
    </SectionCard>
  );
}

function DuplicateGroupRow({ group }: { group: DqDuplicateGroup }) {
  const merge = useMergeOrganizationsMutation();
  const [survivorId, setSurvivorId] = useState(group.suggested_survivor_id);
  const loserIds = group.members.map((m) => m.org_id).filter((id) => id !== survivorId);

  return (
    <li className="flex flex-wrap items-center gap-co-6 rounded-md border border-border/60 bg-muted/20 px-co-8 py-co-6 text-13">
      {group.members.map((m) => {
        const survivor = m.org_id === survivorId;
        return (
          <button
            type="button"
            key={m.org_id}
            onClick={() => setSurvivorId(m.org_id)}
            title={survivor ? "Survivor — kept" : "Click to keep this one instead"}
            className={cn(
              "focus-ring inline-flex items-center gap-co-4 rounded-[var(--radius-sm)] px-co-6 py-0.5 text-12 transition-colors",
              survivor
                ? "border border-success/50 bg-success/10 font-medium text-foreground"
                : "border border-border bg-card text-muted-foreground hover:border-muted-foreground/40 hover:text-foreground",
            )}
          >
            {survivor ? <Check className="h-3 w-3 text-success" /> : null}
            {m.name}
            {typeof m.edge_count === "number" && m.edge_count > 0 ? (
              <span className="text-10 text-muted-foreground/70">{m.edge_count}</span>
            ) : null}
          </button>
        );
      })}
      <span className="ml-auto">
        <ApplyButton
          idleLabel={`Merge ${loserIds.length}`}
          confirmLabel="Confirm merge"
          busy={merge.isPending}
          disabled={loserIds.length === 0}
          onApply={() => merge.mutate({ survivorId, loserIds })}
        />
      </span>
    </li>
  );
}

function DuplicatePeopleSection({ data }: { data: DataQualityReport }) {
  const groups = data.person_duplicate_groups ?? [];
  if (groups.length === 0) {
    return (
      <SectionCard
        icon={Users}
        title="Duplicate people"
        subtitle="No people share an email address."
        clean
      />
    );
  }
  const g = data.summary;
  return (
    <SectionCard
      icon={Users}
      title="Duplicate people"
      subtitle={`${g.person_duplicate_groups} group${g.person_duplicate_groups === 1 ? "" : "s"} (${g.duplicate_people} people) share an email — open each to reconcile`}
      action={
        <Badge variant="outline" className="rounded-[var(--radius-sm)] text-12 text-muted-foreground">
          Review
        </Badge>
      }
    >
      <ul className="space-y-co-8">
        {groups.slice(0, 12).map((grp) => (
          <PersonDuplicateGroupRow key={grp.key} group={grp} />
        ))}
      </ul>
      {groups.length > 12 ? (
        <p className="pt-co-8 text-12 text-muted-foreground">
          + <MonoNumeric tone="muted">{groups.length - 12}</MonoNumeric> more groups
        </p>
      ) : null}
    </SectionCard>
  );
}

function PersonDuplicateGroupRow({ group }: { group: DqPersonDuplicateGroup }) {
  const merge = useMergePeopleMutation();
  const [survivorId, setSurvivorId] = useState(group.suggested_survivor_id);
  const loserIds = group.members.map((m) => m.person_id).filter((id) => id !== survivorId);

  return (
    <li className="flex flex-wrap items-center gap-co-6 rounded-md border border-border/60 bg-muted/20 px-co-8 py-co-6 text-13">
      <span className="inline-flex items-center gap-co-4 font-mono text-12 text-muted-foreground" title={group.shared_email}>
        <Hash className="h-3 w-3 shrink-0" />
        {group.shared_email}
      </span>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      {group.members.map((m) => {
        const survivor = m.person_id === survivorId;
        return (
          <button
            type="button"
            key={m.person_id}
            onClick={() => setSurvivorId(m.person_id)}
            title={survivor ? "Survivor — kept" : "Click to keep this one instead"}
            className={cn(
              "focus-ring inline-flex items-center gap-co-4 rounded-[var(--radius-sm)] px-co-6 py-0.5 text-12 transition-colors",
              survivor
                ? "border border-success/50 bg-success/10 font-medium text-foreground"
                : "border border-border bg-card text-muted-foreground hover:border-muted-foreground/40 hover:text-foreground",
            )}
          >
            {survivor ? <Check className="h-3 w-3 shrink-0 text-success" /> : null}
            {m.name}
          </button>
        );
      })}
      <span className="ml-auto">
        <ApplyButton
          idleLabel={`Merge ${loserIds.length}`}
          confirmLabel="Confirm merge"
          busy={merge.isPending}
          disabled={loserIds.length === 0}
          onApply={() => merge.mutate({ survivorId, loserIds })}
        />
      </span>
    </li>
  );
}

function JobTitleOrgSection({ data }: { data: DataQualityReport }) {
  const items = data.issues.job_title_as_name ?? [];
  if (items.length === 0) return null;
  return (
    <SectionCard
      icon={AlertTriangle}
      title="Job titles used as organizations"
      subtitle={`${items.length} org${items.length === 1 ? "" : "s"} look like a job title parsed into the company field — review or rename`}
    >
      <ul className="flex flex-wrap gap-co-6">
        {items.map((o) => (
          <li key={o.org_id}>
            <Badge variant="outline" className="rounded-[var(--radius-sm)] border-warning/40 bg-warning/10 text-12 text-warning">
              {o.name}
            </Badge>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function PhoneNameSection({ data }: { data: DataQualityReport }) {
  const items = data.issues.phone_as_name;
  if (items.length === 0) return null;
  return (
    <SectionCard
      icon={AlertTriangle}
      title="Phone numbers used as names"
      subtitle={`${items.length} org${items.length === 1 ? "" : "s"} named with a phone number — fix or merge manually`}
    >
      <ul className="flex flex-wrap gap-co-6">
        {items.map((o) => (
          <li key={o.org_id}>
            <Badge variant="outline" className="rounded-[var(--radius-sm)] border-warning/40 bg-warning/10 font-mono text-12 text-warning">
              {o.name}
            </Badge>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}
