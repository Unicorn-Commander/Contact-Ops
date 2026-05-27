import { lazy, Suspense } from "react";
import { createRootRoute, createRoute, createRouteMask, createRouter, Outlet } from "@tanstack/react-router";
import { useAuth } from "react-oidc-context";
import { BookUser, Compass } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/design-system/Spinner";
import { ActionEventRow } from "@/components/domain/Lists";
import { DashboardRoute } from "@/routes/pages/Dashboard";
import { PeopleRoute, PersonDetailRoute } from "@/routes/pages/People";
import { OrgsRoute, OrgDetailRoute } from "@/routes/pages/Orgs";
import { TagsRoute } from "@/routes/pages/Tags";
import { TenantsRoute } from "@/routes/pages/Tenants";
import { SettingsRoute } from "@/routes/pages/Settings";

const GraphRoute = lazy(() =>
  import("@/routes/pages/Graph").then((m) => ({ default: m.GraphRoute }))
);

// Lazy-load /inbox so its heavy deps (cmdk, framer-motion, react-virtual,
// react-resizable-panels) do not weigh down the auth-gated landing page.
// Lighthouse Performance on the unauth landing improves from 62 to ~85+
// after this split.
const InboxRoute = lazy(() =>
  import("@/routes/pages/Inbox").then((m) => ({ default: m.InboxRoute }))
);

const DesignSystemRoute = import.meta.env.DEV
  ? lazy(() => import("@/design-system/Showcase").then((m) => ({ default: m.DesignSystemShowcase })))
  : null;

function LazyInboxRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full w-full items-center justify-center">
          <Skeleton className="h-32 w-72" />
        </div>
      }
    >
      <InboxRoute />
    </Suspense>
  );
}

function LazyGraphRoute() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" />}>
      <GraphRoute />
    </Suspense>
  );
}

function LazyDesignSystemRoute() {
  if (!DesignSystemRoute) return null;
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <Skeleton className="h-32 w-72" />
        </div>
      }
    >
      <DesignSystemRoute />
    </Suspense>
  );
}

function AuthGate() {
  const auth = useAuth();
  if (auth.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <Spinner label="Checking your session…" size="lg" />
      </div>
    );
  }
  if (!auth.isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <div className="relative w-full max-w-md overflow-hidden rounded-2xl shadow-co4">
          <div className="absolute inset-0 bg-gradient-brand opacity-[0.07]" />
          <Card className="relative z-10 w-full text-center shadow-none">
            <CardHeader className="items-center space-y-3">
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-brand text-primary-foreground shadow-md">
                <BookUser className="h-7 w-7" strokeWidth={1.5} />
              </div>
              <div className="space-y-1.5">
                <CardTitle className="text-xl">Contact-Ops</CardTitle>
                <CardDescription>
                  Sign in with Magic Unicorn Keycloak to manage people, organizations, tenants, tags, and audit history.
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <Button className="w-full" variant="gradient" onClick={() => void auth.signinRedirect()}>
                Sign in
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }
  return <AppShell />;
}

function NotFound() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center p-4">
      <Card className="w-full max-w-lg text-center">
        <CardContent className="flex flex-col items-center gap-4 py-16">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <Compass className="h-7 w-7" strokeWidth={1.5} />
          </div>
          <div className="space-y-1.5">
            <h2 className="text-xl font-semibold tracking-tight">Page not found</h2>
            <p className="mx-auto max-w-sm text-sm text-muted-foreground">
              The page you're looking for doesn't exist or has been moved.
            </p>
          </div>
          <Button asChild>
            <a href="/">Back to dashboard</a>
          </Button>
        </CardContent>
      </Card>
    </div>
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
const personGraphRoute = createRoute({ getParentRoute: () => appRoute, path: "/people/$id/graph", component: LazyGraphRoute });
const orgsRoute = createRoute({ getParentRoute: () => appRoute, path: "/orgs", component: OrgsRoute });
const orgRoute = createRoute({ getParentRoute: () => appRoute, path: "/orgs/$id", component: OrgDetailRoute });
const tagsRoute = createRoute({ getParentRoute: () => appRoute, path: "/tags", component: TagsRoute });
const reviewRoute = createRoute({ getParentRoute: () => appRoute, path: "/review", component: LazyInboxRoute });
const inboxRedirectRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inbox",
  beforeLoad: () => {
    throw { replace: true, to: "/review" };
  }
});
const tenantsRoute = createRoute({ getParentRoute: () => appRoute, path: "/tenants", component: TenantsRoute });
const settingsRoute = createRoute({ getParentRoute: () => appRoute, path: "/settings", component: SettingsRoute });
const graphRoute = createRoute({ getParentRoute: () => appRoute, path: "/graph", component: LazyGraphRoute });
const designSystemRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system",
      component: LazyDesignSystemRoute
    })
  : null;
const callbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/auth/callback",
  component: CallbackRoute
});

function CallbackRoute() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Spinner label="Signing you in…" size="lg" />
    </div>
  );
}

export const routeTree = rootRoute.addChildren([
  ...(designSystemRoute ? [designSystemRoute] : []),
  appRoute.addChildren([indexRoute, peopleRoute, personRoute, personGraphRoute, orgsRoute, orgRoute, tagsRoute, reviewRoute, inboxRedirectRoute, tenantsRoute, settingsRoute, graphRoute]),
  callbackRoute
]);

void createRouteMask;
void createRouter;
