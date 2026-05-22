import { createRootRoute, createRoute, createRouteMask, createRouter, Outlet } from "@tanstack/react-router";
import { useAuth } from "react-oidc-context";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ActionEventRow } from "@/components/domain/Lists";
import { DashboardRoute } from "@/routes/pages/Dashboard";
import { PeopleRoute, PersonDetailRoute } from "@/routes/pages/People";
import { OrgsRoute, OrgDetailRoute } from "@/routes/pages/Orgs";
import { TagsRoute } from "@/routes/pages/Tags";
import { TenantsRoute } from "@/routes/pages/Tenants";
import { SettingsRoute } from "@/routes/pages/Settings";
import { InboxRoute } from "@/routes/pages/Inbox";

function AuthGate() {
  const auth = useAuth();
  if (auth.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="w-full max-w-md space-y-3">
          <Skeleton className="h-10 w-48" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    );
  }
  if (!auth.isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>Contact-Ops</CardTitle>
            <CardDescription>Sign in with Magic Unicorn Keycloak to manage people, organizations, tenants, tags, and audit history.</CardDescription>
          </CardHeader>
          <CardContent>
            <Button className="w-full" onClick={() => void auth.signinRedirect()}>
              Sign in
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }
  return <AppShell />;
}

function NotFound() {
  return (
    <Card className="text-center py-12">
      <div className="mb-6">
        <svg 
          className="h-12 w-12 mx-auto text-violet-400" 
          fill="none" 
          stroke="currentColor" 
          viewBox="0 0 24 24"
        >
          <path 
            strokeLinecap="round" 
            strokeLinejoin="round" 
            strokeWidth={2} 
            d="M12 8v4l3 3"
          />
        </svg>
      </div>
      <h2 className="mb-4 text-xl font-semibold text-violet-600">Page not found</h2>
      <p className="mb-6 text-muted-foreground max-w-xl">
        The page you're looking for doesn't exist or has been moved.
      </p>
      <Button 
        className="bg-violet-500 hover:bg-violet-600"
        asChild
      >
        <a href="/">Back to dashboard</a>
      </Button>
    </Card>
  );
}

export function AuditList({ events }: { events?: Array<Record<string, unknown>> }) {
  return <div>{events?.length ? events.map((event, index) => <ActionEventRow key={String(event.event_id ?? index)} event={event} />) : <p className="text-sm text-muted-foreground">No audit events returned.</p>}</div>;
}

const rootRoute = createRootRoute({
  component: () => (
    <>
      <Outlet />
    </>
  ),
  notFoundComponent: NotFound
});

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  component: AuthGate
});

const indexRoute = createRoute({ getParentRoute: () => appRoute, path: "/", component: DashboardRoute });
const peopleRoute = createRoute({ getParentRoute: () => appRoute, path: "/people", component: PeopleRoute });
const personRoute = createRoute({ getParentRoute: () => appRoute, path: "/people/$id", component: PersonDetailRoute });
const orgsRoute = createRoute({ getParentRoute: () => appRoute, path: "/orgs", component: OrgsRoute });
const orgRoute = createRoute({ getParentRoute: () => appRoute, path: "/orgs/$id", component: OrgDetailRoute });
const tagsRoute = createRoute({ getParentRoute: () => appRoute, path: "/tags", component: TagsRoute });
const inboxRoute = createRoute({ getParentRoute: () => appRoute, path: "/inbox", component: InboxRoute });
const tenantsRoute = createRoute({ getParentRoute: () => appRoute, path: "/tenants", component: TenantsRoute });
const settingsRoute = createRoute({ getParentRoute: () => appRoute, path: "/settings", component: SettingsRoute });
const callbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/auth/callback",
  component: CallbackRoute
});

function CallbackRoute() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="text-center space-y-4">
        <div className="h-8 w-8">
          <Skeleton className="h-full w-full rounded-full" />
        </div>
        <p className="text-sm text-muted-foreground">
          Signing in... Please wait.
        </p>
      </div>
    </div>
  );
}

export const routeTree = rootRoute.addChildren([
  appRoute.addChildren([indexRoute, peopleRoute, personRoute, orgsRoute, orgRoute, tagsRoute, inboxRoute, tenantsRoute, settingsRoute]),
  callbackRoute
]);

void createRouteMask;
void createRouter;
