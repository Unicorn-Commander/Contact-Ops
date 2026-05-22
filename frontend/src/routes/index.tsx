import { createRootRoute, createRoute, createRouteMask, createRouter, Outlet, redirect } from "@tanstack/react-router";
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
    <Card>
      <CardHeader>
        <CardTitle>That page is not in Contact-Ops</CardTitle>
        <CardDescription>The route may be from a future phase or the URL may be mistyped.</CardDescription>
      </CardHeader>
      <CardContent>
        <Button asChild>
          <a href="/">Back to dashboard</a>
        </Button>
      </CardContent>
    </Card>
  );
}

function InboxPlaceholder() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Approval inbox</CardTitle>
        <CardDescription>No agent suggestions yet. Phase 3 lands the agents.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
          Suggestion cards will mount here without changing the shell or navigation.
        </div>
      </CardContent>
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
const inboxRoute = createRoute({ getParentRoute: () => appRoute, path: "/inbox", component: InboxPlaceholder });
const tenantsRoute = createRoute({ getParentRoute: () => appRoute, path: "/tenants", component: TenantsRoute });
const settingsRoute = createRoute({ getParentRoute: () => appRoute, path: "/settings", component: SettingsRoute });
const callbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/auth/callback",
  beforeLoad: () => {
    throw redirect({ to: "/" });
  }
});

export const routeTree = rootRoute.addChildren([
  appRoute.addChildren([indexRoute, peopleRoute, personRoute, orgsRoute, orgRoute, tagsRoute, inboxRoute, tenantsRoute, settingsRoute]),
  callbackRoute
]);

void createRouteMask;
void createRouter;
