import { Activity, Building2, GitMerge, Inbox, Users } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "react-oidc-context";
import { useDashboard, useRecentEvents, useToolsList } from "@/hooks/useMcp";
import { AuditList } from "@/routes";
import { cn } from "@/lib/utils";

function Stat({ label, value, icon: Icon, isPrimary = false }: { label: string; value: string | number; icon: typeof Users; isPrimary?: boolean }) {
  return (
    <Card className={cn(isPrimary && "border-l-2 border-violet-500 bg-violet-50/50")}>
      <CardContent className="flex items-center justify-between p-5">
        <div>
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums {isPrimary ? 'text-violet-500' : ''}">{value}</p>
        </div>
        <div className="rounded-md bg-muted p-2">
          <Icon className={`h-5 w-5 {isPrimary ? 'text-violet-500' : 'text-muted-foreground'}`} />
        </div>
      </CardContent>
    </Card>
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
    return <Skeleton className="h-[520px] w-full" />;
  }

  return (
    <div className="space-y-6">
      <div className="mb-4">
        <h1 className="text-2xl font-semibold">{getTimeOfDayGreeting()}, {displayName}</h1>
        <p className="text-sm text-muted-foreground">
          {tools.data?.tools?.length ?? 0} MCP tools visible to the authenticated user.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Stat label="People" value={dashboard.data?.people.total_count ?? dashboard.data?.people.count ?? 0} icon={Users} isPrimary={true} />
        <Stat label="Organizations" value={dashboard.data?.orgs.count ?? 0} icon={Building2} />
        <Stat label="Recent merges" value="0" icon={GitMerge} />
        <Stat label="Pending suggestions" value="Phase 3" icon={Inbox} />
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-4 w-4" />
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
