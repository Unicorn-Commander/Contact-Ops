import { Link, useNavigate } from "@tanstack/react-router";
import {
  Activity,
  Building2,
  CheckCircle2,
  ChevronRight,
  ListChecks,
  Plug,
  Plus,
  Sparkles,
  Upload,
  UserPlus,
  Users,
  Wand2
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useMemo } from "react";
import { useAuth } from "react-oidc-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboard, useDataCoverage, useRecentEvents, useToolsList } from "@/hooks/useMcp";
import { useApproveMutation, useInboxList, useRejectMutation, type InboxFilters } from "@/hooks/useInbox";
import { useConnectors } from "@/hooks/useConnectors";
import { AuditList } from "@/routes";
import { cn } from "@/lib/utils";
import { AmbientGraph } from "@/design-system/v2/AmbientGraph";
import { CountUp } from "@/design-system/v2/CountUp";
import { useAppShell } from "@/components/app-shell-context";
import {
  AtAGlance,
  DataHealth,
  LiveStatusLine,
  OnboardingState,
  ProposalRow,
  type GlanceHighlight
} from "@/components/dashboard";

type ValidRoute = "/people" | "/orgs" | "/review" | "/settings";

const PROPOSAL_FILTERS: InboxFilters = { status: "proposed" };

/**
 * A single count-up stat tile for the v2 command-center hero. Layered glass over
 * the ambient graph backdrop; the number animates from 0 → value once on
 * scroll-into-view (CountUp respects prefers-reduced-motion → final value, no
 * animation). Drills into `to` when provided.
 *
 * Exported so the dev-only dashboard-hero preview can mount the EXACT production
 * tile (not a twin) without auth or a backend.
 */
export function HeroStat({
  icon: Icon,
  value,
  decimals,
  suffix,
  label,
  sub,
  to
}: {
  icon: LucideIcon;
  value: number;
  decimals?: number;
  suffix?: string;
  label: string;
  sub?: string;
  to?: ValidRoute;
}) {
  const body = (
    <div className="co-v2-glass co-v2-glass-edge relative h-full overflow-hidden p-5 transition-shadow group-hover:shadow-[var(--co-v2-shadow-cinematic)]">
      <div className="flex items-center justify-between">
        <span className="flex h-9 w-9 items-center justify-center rounded-[var(--radius-md)] border border-[oklch(var(--co-brand-300)/0.3)] bg-[oklch(var(--co-brand-500)/0.12)] text-[oklch(var(--co-brand-300))]">
          <Icon className="h-[18px] w-[18px]" strokeWidth={1.8} />
        </span>
        <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-muted-foreground">{label}</span>
      </div>
      <div className="mt-4 text-3xl font-bold tracking-tight text-foreground">
        <CountUp value={value} decimals={decimals} suffix={suffix} />
      </div>
      {sub ? <p className="mt-1 text-xs text-muted-foreground">{sub}</p> : null}
    </div>
  );
  if (!to) return body;
  return (
    <Link to={to} aria-label={`Open ${label}`} className="focus-ring group block rounded-[var(--radius-lg)]">
      {body}
    </Link>
  );
}

/**
 * v2 command-center hero — the cinematic dashboard brand surface. Greeting +
 * "Your relationship graph at a glance" + real count-up stat tiles over an
 * ambient relationship-graph backdrop, with the "Ask the Analyst" and "Review
 * queue" actions. Tiles are passed in already filtered to metrics that actually
 * exist (the caller omits any it can't back with real data — no fabricated
 * numbers). The graph + bloom are decorative (aria-hidden) and reduced-motion
 * gated; all text sits on .co-v2-stage-content with its own AA contrast.
 *
 * Exported so the dev-only dashboard-hero preview can mount the EXACT production
 * hero (not a twin) without auth or a backend.
 */
export function CommandCenterHero({
  greeting,
  name,
  tiles,
  onAskAnalyst
}: {
  greeting: string;
  name: string;
  tiles: React.ReactNode;
  onAskAnalyst: () => void;
}) {
  return (
    <div className="co-v2-ambient relative overflow-hidden rounded-[var(--radius-lg)] border border-[oklch(var(--co-v2-glass-hairline))] shadow-[var(--shadow-3)]">
      <AmbientGraph nodeCount={24} linkDistance={0.26} className="opacity-60" />
      <div className="co-v2-stage-content p-6 lg:p-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-sm text-muted-foreground">
              {greeting}, <span className="font-medium text-foreground">{name}</span>
            </p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight text-foreground lg:text-3xl">
              Your relationship graph at a glance
            </h1>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="gradient" size="sm" className="co-v2-hover-bloom" onClick={onAskAnalyst}>
              <Wand2 className="h-3.5 w-3.5" strokeWidth={2} />
              Ask the Analyst
            </Button>
            <Button
              asChild
              variant="outline"
              size="sm"
              className="border-[oklch(var(--co-v2-glass-hairline-strong))] bg-[oklch(var(--co-v2-glass-fill))] backdrop-blur"
            >
              <Link to="/review">
                <ListChecks className="h-3.5 w-3.5" strokeWidth={2} />
                Review queue
              </Link>
            </Button>
          </div>
        </div>

        <div className="mt-7 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{tiles}</div>
      </div>
    </div>
  );
}

function QuickAction({
  to,
  icon: Icon,
  children
}: {
  to: ValidRoute;
  icon: LucideIcon;
  children: React.ReactNode;
}) {
  return (
    <Button asChild variant="outline" className="w-full justify-start gap-2.5">
      <Link to={to}>
        <Icon className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
        {children}
      </Link>
    </Button>
  );
}

/** A single agent/integration status row in the right rail. */
function StatusRow({ label, detail, ok }: { label: string; detail: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2 text-sm">
      <span className="inline-flex items-center gap-2">
        <CheckCircle2
          className={cn("h-4 w-4", ok ? "text-[oklch(var(--co-emerald-500))]" : "text-muted-foreground")}
          strokeWidth={1.8}
        />
        <span className="text-foreground">{label}</span>
      </span>
      <span className="truncate text-xs text-muted-foreground">{detail}</span>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-4 w-56" />
      </div>
      <Skeleton className="h-11 w-full max-w-2xl rounded-md" />
      <Skeleton className="h-28 w-full rounded-xl" />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-[104px] w-full rounded-lg" />
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <Skeleton className="h-72 w-full rounded-lg lg:col-span-2" />
        <Skeleton className="h-72 w-full rounded-lg" />
      </div>
    </div>
  );
}

export function DashboardRoute() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const dashboard = useDashboard();
  const events = useRecentEvents();
  const tools = useToolsList();
  const proposals = useInboxList(PROPOSAL_FILTERS);
  const approve = useApproveMutation(PROPOSAL_FILTERS);
  const reject = useRejectMutation(PROPOSAL_FILTERS);
  // "Ask the Analyst" opens the SAME shell-owned panel as the top-bar Sparkles
  // button / ⌘J — never a duplicate instance.
  const { openAnalyst } = useAppShell();

  const getTimeOfDayGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return "Good morning";
    if (hour < 18) return "Good afternoon";
    return "Good evening";
  };

  const displayName = user?.profile?.name ?? user?.profile?.preferred_username ?? user?.profile?.email ?? "there";

  const pendingCount = useMemo(() => {
    const pages = proposals.data?.pages;
    if (!pages?.length) return undefined;
    return pages[0]?.total_estimate ?? 0;
  }, [proposals.data]);

  const recentProposals = useMemo(() => {
    const pages = proposals.data?.pages;
    if (!pages?.length) return [];
    return pages.flatMap((p) => p.proposals ?? []).slice(0, 4);
  }, [proposals.data]);

  // Distinct agents currently proposing changes (across all loaded pages).
  const agentCount = useMemo(() => {
    const pages = proposals.data?.pages;
    if (!pages?.length) return 0;
    const ids = new Set<string>();
    for (const page of pages) for (const p of page.proposals ?? []) if (p.agent_id) ids.add(p.agent_id);
    return ids.size;
  }, [proposals.data]);

  // Hooks must run unconditionally before any early-return branch — react-hooks
  // rules-of-hooks. Move connector lookup above the loading-skeleton return.
  const connectors = useConnectors();
  const connectorRows = connectors.data?.items ?? [];
  const configuredConnectors = connectorRows.filter((c) => c.status === "configured");
  // Real field-coverage for the "Data health" card (sampled + labelled honestly).
  const coverage = useDataCoverage();

  if (dashboard.isLoading) {
    return <DashboardSkeleton />;
  }

  const peopleCount = dashboard.data?.people.total_count ?? dashboard.data?.people.count ?? 0;
  const orgCount = dashboard.data?.orgs.total_count ?? dashboard.data?.orgs.count ?? 0;
  const toolCount = tools.data?.tools?.length ?? 0;
  const eventItems = events.data?.items ?? [];
  const pending = pendingCount ?? 0;

  // Truly fresh tenant means NO connectors configured AND no work in flight.
  // Just because peopleCount is 0 doesn't mean we should nag — they may have
  // 800 proposals waiting in /review or 3 connectors configured that haven't
  // landed approvals yet. Don't show the "go set things up" onboarding when
  // setup is clearly already done.
  const isEmptyTenant =
    peopleCount === 0 &&
    orgCount === 0 &&
    pending === 0 &&
    configuredConnectors.length === 0;

  // AI "at a glance" — built only from signals the registry actually exposes.
  // Likely-duplicates / stale records require backend metrics we don't have on
  // this endpoint yet, so they are intentionally omitted rather than faked; the
  // card degrades to a placeholder when there's nothing meaningful to say.
  const glanceHighlights: GlanceHighlight[] = [
    {
      id: "people",
      icon: Users,
      value: peopleCount.toLocaleString(),
      label: "people",
      tone: "fuchsia",
      onClick: () => void navigate({ to: "/people" })
    },
    {
      id: "orgs",
      icon: Building2,
      value: orgCount.toLocaleString(),
      label: "organizations",
      tone: "emerald",
      onClick: () => void navigate({ to: "/orgs" })
    }
  ];
  if (pending > 0) {
    glanceHighlights.push({
      id: "pending",
      icon: ListChecks,
      value: pending.toLocaleString(),
      label: "pending reviews",
      tone: "amber",
      onClick: () => void navigate({ to: "/review" })
    });
  }
  if (agentCount > 0) {
    glanceHighlights.push({
      id: "agents",
      icon: Sparkles,
      value: agentCount.toLocaleString(),
      label: agentCount === 1 ? "agent active" : "agents active",
      tone: "sky"
    });
  }

  const hasGlanceSignal = peopleCount > 0 || orgCount > 0 || pending > 0;
  const glanceSummary = hasGlanceSignal
    ? `You're tracking ${peopleCount.toLocaleString()} ${peopleCount === 1 ? "person" : "people"} across ${orgCount.toLocaleString()} ${orgCount === 1 ? "organization" : "organizations"}.` +
      (pending > 0
        ? ` ${pending.toLocaleString()} ${pending === 1 ? "proposal is" : "proposals are"} waiting on your review${
            agentCount > 0 ? ` from ${agentCount} ${agentCount === 1 ? "agent" : "agents"}` : ""
          }.`
        : " The review queue is clear — agents are keeping records clean.")
    : undefined;

  // v2 command-center hero stat tiles — built ONLY from metrics the registry
  // actually exposes. People/Organizations are core counts (always real).
  // Pending reviews is shown only once the inbox query has resolved (a real 0 =
  // "queue clear"; undefined = not-yet-loaded → omit, never faked). Agent
  // activity is the count of distinct agents in the loaded proposals. There is
  // deliberately NO "graph health" tile — the dashboard endpoint exposes no such
  // metric, so we omit it rather than invent a percentage.
  const heroTiles = (
    <>
      <HeroStat icon={Users} value={peopleCount} label="People" to="/people" />
      <HeroStat icon={Building2} value={orgCount} label="Organizations" to="/orgs" />
      {pendingCount !== undefined ? (
        <HeroStat
          icon={ListChecks}
          value={pendingCount}
          label="Pending review"
          sub="awaiting your decision"
          to="/review"
        />
      ) : null}
      <HeroStat icon={Sparkles} value={agentCount} label="Agents active" sub="proposing changes" />
    </>
  );

  return (
    <div className="space-y-6">
      {/* v2 command-center hero — greeting + real count-up tiles over the
          ambient relationship graph, with Ask-the-Analyst + Review-queue. */}
      <CommandCenterHero
        greeting={getTimeOfDayGreeting()}
        name={displayName}
        tiles={heroTiles}
        onAskAnalyst={openAnalyst}
      />

      {/* Calm status + primary-add line beneath the cinematic hero. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <LiveStatusLine live={toolCount > 0} statusLabel={toolCount > 0 ? "Live" : "Offline"} />
          <span className="text-border" aria-hidden>
            ·
          </span>
          <span className="text-xs text-muted-foreground">
            {toolCount ? `${toolCount} MCP tools available` : "Connecting to your registry"}
          </span>
        </div>
        <Button asChild variant="outline" size="sm" className="h-9">
          <Link to="/people">
            <Plus className="h-4 w-4" strokeWidth={1.8} />
            Add person
          </Link>
        </Button>
      </div>

      {isEmptyTenant ? (
        <OnboardingState
          onImport={() => void navigate({ to: "/settings" })}
          onAddPerson={() => void navigate({ to: "/people" })}
          onAddOrg={() => void navigate({ to: "/orgs" })}
        />
      ) : (
        <>
          {/* AI at-a-glance */}
          <AtAGlance
            summary={glanceSummary}
            placeholder={!hasGlanceSignal}
            context={hasGlanceSignal ? "Synthesized from your registry" : undefined}
            highlights={glanceHighlights}
          />

          {/* Main + right rail */}
          <div className="grid gap-6 lg:grid-cols-3">
            <div className="space-y-6 lg:col-span-2">
              {/* Pending proposals */}
              <Card>
                <CardHeader className="flex-row items-center justify-between space-y-0">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <ListChecks className="h-4 w-4 text-[oklch(var(--co-amber-500))]" strokeWidth={1.8} />
                    Pending proposals
                  </CardTitle>
                  <Button asChild variant="ghost" size="sm" className="text-link">
                    <Link to="/review">
                      Review all
                      <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.8} />
                    </Link>
                  </Button>
                </CardHeader>
                <CardContent>
                  {proposals.isLoading ? (
                    <div className="space-y-2.5">
                      {Array.from({ length: 3 }).map((_, i) => (
                        <Skeleton key={i} className="h-14 w-full rounded-md" />
                      ))}
                    </div>
                  ) : recentProposals.length ? (
                    <div className="divide-y divide-border">
                      {recentProposals.map((p) => (
                        <ProposalRow
                          key={p.proposal_id}
                          entityKind={p.entity_kind}
                          entityName={p.entity_display_name}
                          action={p.action_type.replace(/[_.]/g, " ")}
                          agentSlug={p.agent_id}
                          confidence={p.confidence}
                          pending={
                            (approve.isPending && approve.variables?.proposalId === p.proposal_id) ||
                            (reject.isPending && reject.variables?.proposalId === p.proposal_id)
                          }
                          onOpen={() => void navigate({ to: "/review" })}
                          onApprove={() => approve.mutate({ proposalId: p.proposal_id, tier: 1, keyboardPath: false })}
                          onReject={() => reject.mutate({ proposalId: p.proposal_id, mode: "reject", keyboardPath: false })}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-2 py-8 text-center">
                      <CheckCircle2 className="h-8 w-8 text-[oklch(var(--co-emerald-500))]" strokeWidth={1.5} />
                      <p className="text-sm font-medium">Review queue is clear</p>
                      <p className="max-w-xs text-xs text-muted-foreground">
                        Agent proposals land here for your approval. Nothing is waiting right now.
                      </p>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Recent activity */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Activity className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
                    Recent activity
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {events.isLoading ? (
                    <div className="space-y-3">
                      {Array.from({ length: 4 }).map((_, i) => (
                        <Skeleton key={i} className="h-10 w-full rounded-md" />
                      ))}
                    </div>
                  ) : eventItems.length ? (
                    <AuditList events={eventItems} />
                  ) : (
                    <div className="flex flex-col items-center gap-2 py-8 text-center">
                      <Activity className="h-8 w-8 text-muted-foreground" strokeWidth={1.5} />
                      <p className="text-sm font-medium">No activity yet</p>
                      <p className="max-w-xs text-xs text-muted-foreground">
                        Edits, merges, and agent actions across this workspace will show up here.
                      </p>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Right rail */}
            <div className="space-y-6">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Quick actions</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <QuickAction to="/people" icon={UserPlus}>
                    Add person
                  </QuickAction>
                  <QuickAction to="/orgs" icon={Building2}>
                    New organization
                  </QuickAction>
                  <QuickAction to="/review" icon={ListChecks}>
                    Review proposals
                  </QuickAction>
                  <QuickAction to="/settings" icon={Upload}>
                    Import contacts
                  </QuickAction>
                </CardContent>
              </Card>

              {/* Data health — real field coverage (email + organization),
                  sampled from the registry and labelled honestly. */}
              <DataHealth
                coverage={coverage.data?.coverage ?? []}
                note={coverage.data?.note}
                isLoading={coverage.isLoading}
              />

              <Card>
                <CardHeader className="flex-row items-center justify-between space-y-0">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Plug className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
                    Sync &amp; integrations
                  </CardTitle>
                  {configuredConnectors.length > 0 ? (
                    <Button asChild variant="ghost" size="sm" className="text-link">
                      <Link to="/connectors">Manage</Link>
                    </Button>
                  ) : null}
                </CardHeader>
                <CardContent className="divide-y divide-border py-0">
                  <StatusRow
                    label="MCP surface"
                    detail={toolCount ? `${toolCount} tools` : "Unavailable"}
                    ok={toolCount > 0}
                  />
                  {/* Real connector state, not a hardcoded "CardDAV: Connected"
                      banner. Reflects what /connectors actually shows so the
                      dashboard never lies about setup. */}
                  {configuredConnectors.length > 0 ? (
                    <StatusRow
                      label="Contact sources"
                      detail={
                        configuredConnectors
                          .map((c) =>
                            c.provider === "icloud"
                              ? "iCloud"
                              : c.provider === "m365"
                                ? "Microsoft 365"
                                : c.provider === "gmail"
                                  ? "Gmail"
                                  : c.provider
                          )
                          .join(" · ")
                      }
                      ok
                    />
                  ) : (
                    <StatusRow label="Contact sources" detail="None connected" ok={false} />
                  )}
                  <StatusRow
                    label="Agent pipeline"
                    detail={pending ? `${pending.toLocaleString()} proposals queued` : "Idle"}
                    ok
                  />
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
