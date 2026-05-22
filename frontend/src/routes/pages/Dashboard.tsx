import { Activity, Building2, GitMerge, Inbox, Users } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboard, useRecentEvents, useToolsList } from "@/hooks/useMcp";
import { AuditList } from "@/routes";

function Stat({ label, value, icon: Icon }: { label: string; value: string | number; icon: typeof Users }) {
  return (
    <Card>
      <CardContent className="flex items-center justify-between p-5">
        <div>
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold">{value}</p>
        </div>
        <div className="rounded-md bg-muted p-2">
          <Icon className="h-5 w-5" />
        </div>
      </CardContent>
    </Card>
  );
}

export function DashboardRoute() {
  const dashboard = useDashboard();
  const events = useRecentEvents();
  const tools = useToolsList();

  if (dashboard.isLoading) {
    return <Skeleton className="h-[520px] w-full" />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          {tools.data?.tools?.length ?? 0} MCP tools visible to the authenticated user.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Stat label="People" value={dashboard.data?.people.total_count ?? dashboard.data?.people.count ?? 0} icon={Users} />
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
