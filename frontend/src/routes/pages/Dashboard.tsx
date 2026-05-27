import { Link } from "@tanstack/react-router";
import { Activity, Building2, ChevronRight, GitMerge, Inbox, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "react-oidc-context";
import { useDashboard, useRecentEvents, useToolsList } from "@/hooks/useMcp";
import { AuditList } from "@/routes";
import { cn } from "@/lib/utils";

type ValidRoute = "/people" | "/orgs" | "/inbox";

/**
 * Ambient stat tile. Calm and glanceable: a label, a large tabular value, and a
 * muted icon puck. When `to` is set the whole tile is a typed Link that lifts on
 * hover and reveals a chevron — drilling into the underlying screen.
 */
function Stat({
  label,
  value,
  icon: Icon,
  to,
  hint
}: {
  label: string;
  value: string | number;
  icon: LucideIcon;
  to?: ValidRoute;
  hint?: string;
}) {
  const body = (
    <Card className={cn("h-full", to && "transition-all duration-150 group-hover:border-primary/30 group-hover:shadow-co1")}>
      <CardContent className="flex items-start justify-between gap-4 p-5">
        <div className="min-w-0">
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
          {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
        </div>
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <Icon className="h-5 w-5" strokeWidth={1.6} />
        </div>
      </CardContent>
    </Card>
  );

  if (!to) return body;
  return (
    <Link to={to} aria-label={`Open ${label}`} className="focus-ring group relative block rounded-lg">
      {body}
      <ChevronRight
        className="absolute bottom-4 right-4 h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
        strokeWidth={1.6}
        aria-hidden
      />
    </Link>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-4 w-56" />
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-[92px] w-full rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-72 w-full rounded-lg" />
    </div>
  );
}

export function DashboardRoute() {
  const { user } = useAuth();
  const dashboard = useDashboard();
  const events = useRecentEvents();
  const tools = useToolsList();

  const getTimeOfDayGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return "Good morning";
    if (hour < 18) return "Good afternoon";
    return "Good evening";
  };

  const displayName = user?.profile?.name ?? user?.profile?.preferred_username ?? user?.profile?.email ?? "there";

  if (dashboard.isLoading) {
    return <DashboardSkeleton />;
  }

  const peopleCount = dashboard.data?.people.total_count ?? dashboard.data?.people.count ?? 0;
  const orgCount = dashboard.data?.orgs.count ?? 0;
  const toolCount = tools.data?.tools?.length ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          {getTimeOfDayGreeting()}, {displayName}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your canonical registry at a glance{toolCount ? ` · ${toolCount} MCP tools available` : ""}.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Stat label="People" value={peopleCount} icon={Users} to="/people" />
        <Stat label="Organizations" value={orgCount} icon={Building2} to="/orgs" />
        <Stat label="Recent merges" value={0} icon={GitMerge} hint="Last 30 days" />
        <Stat label="Pending suggestions" value="—" icon={Inbox} to="/inbox" hint="Review in inbox" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Activity className="h-4 w-4 text-muted-foreground" strokeWidth={1.6} />
            Recent activity
          </CardTitle>
        </CardHeader>
        <CardContent>
          <AuditList events={events.data?.items} />
        </CardContent>
      </Card>
    </div>
  );
}
