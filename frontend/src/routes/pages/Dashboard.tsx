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
import { useInboxList } from "@/hooks/useInbox";
import { AuditList } from "@/routes";
import { cn } from "@/lib/utils";

type ValidRoute = "/people" | "/orgs" | "/review";

type StatTone = "fuchsia" | "sky" | "amber" | "emerald";

const toneClasses: Record<StatTone, { tile: string; icon: string }> = {
  fuchsia: { tile: "ring-[oklch(var(--co-brand-500)/0.22)]", icon: "text-primary" },
  sky: { tile: "ring-[oklch(var(--co-sky-500)/0.22)]", icon: "text-[oklch(var(--co-sky-500))]" },
  amber: { tile: "ring-[oklch(var(--co-amber-500)/0.22)]", icon: "text-[oklch(var(--co-amber-500))]" },
  emerald: { tile: "ring-[oklch(var(--co-emerald-500)/0.22)]", icon: "text-[oklch(var(--co-emerald-500))]" }
};

/**
 * Command-center stat tile. A semantic-accent icon, a big tabular number, and a
 * label. When `to` is set the whole tile is a typed Link that lifts on hover and
 * reveals a chevron — drilling into the underlying screen.
 */
function Stat({
  label,
  value,
  icon: Icon,
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
  const t = toneClasses[tone];
  const body = (
    <Card
      className={cn(
        "h-full ring-1",
        t.tile,
        to && "transition-all duration-150 group-hover:border-primary/40 group-hover:shadow-co1"
      )}
    >
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
          <Icon className={cn("h-4 w-4", t.icon)} strokeWidth={1.8} />
        </div>
        <p className="mt-2.5 text-3xl font-semibold tabular-nums tracking-tight">{value}</p>
        {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
      </CardContent>
    </Card>
  );

  if (!to) return body;
  return (
    <Link to={to} aria-label={`Open ${label}`} className="focus-ring group relative block rounded-lg">
      {body}
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
  const proposals = useInboxList({ status: "proposed" });

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

  return (
    <div className="space-y-6">
      {/* Hero header — gradient wordmark accent */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {getTimeOfDayGreeting()}, <span className="bg-gradient-brand-text">{displayName}</span>
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Your canonical identity hub at a glance{toolCount ? ` · ${toolCount} MCP tools available` : ""}.
          </p>
        </div>
        <Button asChild variant="gradient" className="h-9">
          <Link to="/people">
            <Plus className="h-4 w-4" strokeWidth={1.8} />
            Add person
          </Link>
        </Button>
      </div>

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

      {/* Stat tiles */}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Stat label="People" value={peopleCount} icon={Users} tone="fuchsia" to="/people" />
        <Stat label="Organizations" value={orgCount} icon={Building2} tone="emerald" to="/orgs" />
        <Stat
          label="Pending reviews"
          value={pendingCount ?? "—"}
          icon={ListChecks}
          tone="amber"
          to="/review"
          hint="Awaiting your decision"
        />
        <Stat
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
                    <Link
                      key={p.proposal_id}
                      to="/review"
                      className="focus-ring -mx-2 flex items-center gap-3 rounded-md px-2 py-2.5 transition-colors hover:bg-muted"
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                        {p.entity_kind === "org" ? (
                          <Building2 className="h-4 w-4" strokeWidth={1.8} />
                        ) : (
                          <Users className="h-4 w-4" strokeWidth={1.8} />
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{p.entity_display_name}</span>
                        <span className="block truncate text-xs text-muted-foreground">
                          <span className="capitalize">{p.action_type.replace(/[_.]/g, " ")}</span>
                          {" · "}
                          <span className="font-mono">{p.agent_id}</span>
                        </span>
                      </span>
                      <span className="co-mono-numeric shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                        {Math.round(p.confidence * 100)}%
                      </span>
                    </Link>
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
              <QuickAction to="/people" icon={Upload}>
                Import contacts
              </QuickAction>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Plug className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
                Sync &amp; integrations
              </CardTitle>
            </CardHeader>
            <CardContent className="divide-y divide-border py-0">
              <StatusRow label="MCP surface" detail={toolCount ? `${toolCount} tools` : "Unavailable"} ok={toolCount > 0} />
              <StatusRow
                label="CardDAV sync"
                detail="Connected"
                ok
              />
              <StatusRow
                label="Agent pipeline"
                detail={pendingCount ? `${pendingCount} pending` : "Idle"}
                ok
              />
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
    </div>
  );
}
