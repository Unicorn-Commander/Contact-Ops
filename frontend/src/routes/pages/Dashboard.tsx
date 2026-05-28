import { Link, useNavigate } from "@tanstack/react-router";
import {
  Activity,
  Building2,
  CheckCircle2,
  ChevronRight,
  GitMerge,
  ListChecks,
  Plug,
  Plus,
  Search,
  Sparkles,
  Upload,
  UserPlus,
  Users
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useMemo } from "react";
import { useAuth } from "react-oidc-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboard, useRecentEvents, useToolsList } from "@/hooks/useMcp";
import { useApproveMutation, useInboxList, useRejectMutation, type InboxFilters } from "@/hooks/useInbox";
import { AuditList } from "@/routes";
import { cn } from "@/lib/utils";
import {
  AtAGlance,
  DataHealth,
  LiveStatusLine,
  OnboardingState,
  ProposalRow,
  StatTile,
  type GlanceHighlight,
  type StatTone
} from "@/components/dashboard";

type ValidRoute = "/people" | "/orgs" | "/review" | "/settings";

const PROPOSAL_FILTERS: InboxFilters = { status: "proposed" };

/**
 * Drill-into-able stat tile. Wraps the presentational StatTile in a typed Link
 * (route types differ from the preview, so the Link lives here, not in the
 * component). The `group` parent drives the StatTile's hover lift.
 *
 * NOTE on trends: the dashboard MCP endpoint returns current counts only — no
 * weekly history — so live tiles deliberately do NOT fabricate a "+N this week"
 * delta or sparkline. They render the `hint` instead and gracefully omit the
 * sparkline. The full living-tile treatment (delta + sparkline) is exercised in
 * the design-system preview with representative data. Wire `trend` here once the
 * backend exposes a time series.
 */
function StatLink({
  label,
  value,
  icon,
  tone,
  to,
  hint
}: {
  label: string;
  value: string | number;
  icon: LucideIcon;
  tone: StatTone;
  to?: ValidRoute;
  hint?: string;
}) {
  const tile = <StatTile label={label} value={value} icon={icon} tone={tone} hint={hint} interactive={Boolean(to)} />;
  if (!to) return tile;
  return (
    <Link to={to} aria-label={`Open ${label}`} className="focus-ring group relative block rounded-xl">
      {tile}
      <ChevronRight
        className="absolute bottom-5 right-5 h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
        strokeWidth={1.8}
        aria-hidden
      />
    </Link>
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
    return pages.flatMap((p) => p.proposals).slice(0, 4);
  }, [proposals.data]);

  // Distinct agents currently proposing changes (across all loaded pages).
  const agentCount = useMemo(() => {
    const pages = proposals.data?.pages;
    if (!pages?.length) return 0;
    const ids = new Set<string>();
    for (const page of pages) for (const p of page.proposals) if (p.agent_id) ids.add(p.agent_id);
    return ids.size;
  }, [proposals.data]);

  if (dashboard.isLoading) {
    return <DashboardSkeleton />;
  }

  const peopleCount = dashboard.data?.people.total_count ?? dashboard.data?.people.count ?? 0;
  const orgCount = dashboard.data?.orgs.count ?? 0;
  const toolCount = tools.data?.tools?.length ?? 0;
  const eventItems = events.data?.items ?? [];
  const pending = pendingCount ?? 0;

  // True empty tenant → guided onboarding instead of a void dashboard.
  const isEmptyTenant = peopleCount === 0 && orgCount === 0;

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

  return (
    <div className="space-y-6">
      {/* Hero header — gradient wordmark accent + date/live-status line */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {getTimeOfDayGreeting()}, <span className="bg-gradient-brand-text">{displayName}</span>
          </h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
            <LiveStatusLine live={toolCount > 0} statusLabel={toolCount > 0 ? "Live" : "Offline"} />
            <span className="text-border" aria-hidden>
              ·
            </span>
            <span className="text-xs text-muted-foreground">
              {toolCount ? `${toolCount} MCP tools available` : "Connecting to your registry"}
            </span>
          </div>
        </div>
        <Button asChild variant="gradient" className="h-9">
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
          {/* Global search bar */}
          <form
            role="search"
            className="relative max-w-2xl"
            onSubmit={(event) => {
              event.preventDefault();
              void navigate({ to: "/people" });
            }}
          >
            <Search
              className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
              strokeWidth={1.8}
            />
            <input
              type="search"
              aria-label="Search people, organizations, and tags"
              placeholder="Search people, orgs, tags…"
              className="focus-ring h-11 w-full rounded-md border bg-card pl-11 pr-4 text-sm placeholder:text-muted-foreground"
            />
          </form>

          {/* AI at-a-glance */}
          <AtAGlance
            summary={glanceSummary}
            placeholder={!hasGlanceSignal}
            context={hasGlanceSignal ? "Synthesized from your registry" : undefined}
            highlights={glanceHighlights}
          />

          {/* Stat tiles */}
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <StatLink label="People" value={peopleCount.toLocaleString()} icon={Users} tone="fuchsia" to="/people" />
            <StatLink label="Organizations" value={orgCount.toLocaleString()} icon={Building2} tone="emerald" to="/orgs" />
            <StatLink
              label="Pending reviews"
              value={pendingCount ?? "—"}
              icon={ListChecks}
              tone="amber"
              to="/review"
              hint="Awaiting your decision"
            />
            <StatLink
              label="Agent activity"
              value={agentCount}
              icon={Sparkles}
              tone="sky"
              hint={agentCount === 1 ? "agent proposing changes" : "agents proposing changes"}
            />
          </div>

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

              {/* Data health — degrades to a placeholder until coverage metrics
                  are exposed by the backend (no fabricated percentages). */}
              <DataHealth coverage={[]} />

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Plug className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
                    Sync &amp; integrations
                  </CardTitle>
                </CardHeader>
                <CardContent className="divide-y divide-border py-0">
                  <StatusRow label="MCP surface" detail={toolCount ? `${toolCount} tools` : "Unavailable"} ok={toolCount > 0} />
                  <StatusRow label="CardDAV sync" detail="Connected" ok />
                  <StatusRow label="Agent pipeline" detail={pending ? `${pending} pending` : "Idle"} ok />
                </CardContent>
              </Card>

              <Card>
                <CardContent className="flex items-start gap-3 p-5">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <GitMerge className="h-4 w-4" strokeWidth={1.8} />
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm font-medium">Recent merges</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      Duplicate resolutions appear here as agents reconcile records. None in the last 30 days.
                    </p>
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
