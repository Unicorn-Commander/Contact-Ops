import { lazy, Suspense, useEffect } from "react";
import { createRootRoute, createRoute, createRouteMask, createRouter, Outlet, useNavigate } from "@tanstack/react-router";
import { useAuth } from "react-oidc-context";
import { BookUser, Compass } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/design-system/Spinner";
import { ActionEventRow } from "@/components/domain/Lists";
import { DashboardRoute } from "@/routes/pages/Dashboard";
import { PeopleRoute, PersonDetailRoute } from "@/routes/pages/People";
import { OrgsRoute, OrgDetailRoute } from "@/routes/pages/Orgs";
import { TagsRoute } from "@/routes/pages/Tags";
import { TenantsRoute } from "@/routes/pages/Tenants";
import { SettingsRoute } from "@/routes/pages/Settings";
import { ImportRoute } from "@/routes/pages/Import";
import { ConnectorsRoute } from "@/routes/pages/Connectors";

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

// AI Analyst auth-free preview lives on a dedicated sub-route so the
// screenshot harness can capture it without touching the main Showcase file.
// The Showcase will pick it up via the orchestrator wiring at integration time.
const AIAnalystPreviewRoute = import.meta.env.DEV
  ? lazy(() =>
      import("@/design-system/AIAnalystPreview").then((m) => ({ default: m.AIAnalystPreview }))
    )
  : null;

// Notes & Log auth-free preview — same shape as AIAnalystPreviewRoute so the
// orchestrator can wire it into the Showcase tab list when both tracks land.
const NotesPreviewRoute = import.meta.env.DEV
  ? lazy(() =>
      import("@/design-system/NotesPreview").then((m) => ({ default: m.NotesPreview }))
    )
  : null;

// vCard Import auth-free preview — mirrors AIAnalystPreviewRoute / NotesPreviewRoute
// so the Showcase tab + screenshot harness can capture every state offline.
const ImportPreviewRoute = import.meta.env.DEV
  ? lazy(() =>
      import("@/design-system/ImportPreview").then((m) => ({ default: m.ImportPreview }))
    )
  : null;

// Connectors auth-free preview — same screenshot-without-auth pattern as the
// other previews. The orchestrator wires the Showcase tab in a follow-up.
const ConnectorsPreviewRoute = import.meta.env.DEV
  ? lazy(() =>
      import("@/design-system/ConnectorsPreview").then((m) => ({ default: m.ConnectorsPreview }))
    )
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

function LazyAIAnalystPreviewRoute() {
  if (!AIAnalystPreviewRoute) return null;
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <Skeleton className="h-32 w-72" />
        </div>
      }
    >
      <div className="min-h-screen bg-background p-4 lg:p-6">
        <div className="mx-auto max-w-[1500px]">
          <AIAnalystPreviewRoute />
        </div>
      </div>
    </Suspense>
  );
}

function LazyNotesPreviewRoute() {
  if (!NotesPreviewRoute) return null;
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <Skeleton className="h-32 w-72" />
        </div>
      }
    >
      <div className="min-h-screen bg-background p-4 lg:p-6">
        <div className="mx-auto max-w-[1500px]">
          <NotesPreviewRoute />
        </div>
      </div>
    </Suspense>
  );
}

function LazyImportPreviewRoute() {
  if (!ImportPreviewRoute) return null;
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <Skeleton className="h-32 w-72" />
        </div>
      }
    >
      <div className="min-h-screen bg-background p-4 lg:p-6">
        <div className="mx-auto max-w-[1500px]">
          <ImportPreviewRoute />
        </div>
      </div>
    </Suspense>
  );
}

function LazyConnectorsPreviewRoute() {
  if (!ConnectorsPreviewRoute) return null;
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <Skeleton className="h-32 w-72" />
        </div>
      }
    >
      <div className="min-h-screen bg-background p-4 lg:p-6">
        <div className="mx-auto max-w-[1500px]">
          <ConnectorsPreviewRoute />
        </div>
      </div>
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
        <div className="co-animate-fade-in w-full max-w-md">
          <Card className="border-border">
            <CardContent className="px-8 py-10 text-center">
              <div className="bg-gradient-brand co-glow-brand mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-2xl text-primary-foreground">
                <BookUser className="h-7 w-7" strokeWidth={1.6} />
              </div>
              <h1 className="bg-gradient-brand-text text-3xl font-bold tracking-tight">Contact-Ops</h1>
              <p className="mx-auto mt-2.5 max-w-xs text-sm leading-relaxed text-muted-foreground">
                The canonical identity hub. Sign in with Magic Unicorn to manage people, organizations, and the
                agents that keep them clean.
              </p>
              <Button className="mt-8 h-11 w-full" variant="gradient" onClick={() => void auth.signinRedirect()}>
                Sign in
              </Button>
              <p className="mt-4 text-xs text-muted-foreground">Secured by Magic Unicorn SSO</p>
            </CardContent>
          </Card>
          <p className="mt-4 text-center text-xs text-muted-foreground">by Magic Unicorn</p>
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
const importRoute = createRoute({ getParentRoute: () => appRoute, path: "/import", component: ImportRoute });
const connectorsRoute = createRoute({ getParentRoute: () => appRoute, path: "/connectors", component: ConnectorsRoute });
const designSystemRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system",
      component: LazyDesignSystemRoute
    })
  : null;
const designSystemAIAnalystRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/ai-analyst",
      component: LazyAIAnalystPreviewRoute
    })
  : null;
const designSystemNotesRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/notes",
      component: LazyNotesPreviewRoute
    })
  : null;
const designSystemImportRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/import",
      component: LazyImportPreviewRoute
    })
  : null;
const designSystemConnectorsRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/connectors",
      component: LazyConnectorsPreviewRoute
    })
  : null;
const callbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/auth/callback",
  component: CallbackRoute
});

function CallbackRoute() {
  const auth = useAuth();
  const navigate = useNavigate();
  // react-oidc-context completes the code exchange here but does not redirect on
  // its own — once authenticated, leave /auth/callback for the app.
  useEffect(() => {
    if (!auth.isLoading && auth.isAuthenticated) {
      void navigate({ to: "/", replace: true });
    }
  }, [auth.isLoading, auth.isAuthenticated, navigate]);
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Spinner label="Signing you in…" size="lg" />
    </div>
  );
}

export const routeTree = rootRoute.addChildren([
  ...(designSystemAIAnalystRoute ? [designSystemAIAnalystRoute] : []),
  ...(designSystemNotesRoute ? [designSystemNotesRoute] : []),
  ...(designSystemImportRoute ? [designSystemImportRoute] : []),
  ...(designSystemConnectorsRoute ? [designSystemConnectorsRoute] : []),
  ...(designSystemRoute ? [designSystemRoute] : []),
  appRoute.addChildren([indexRoute, peopleRoute, personRoute, personGraphRoute, orgsRoute, orgRoute, tagsRoute, reviewRoute, inboxRedirectRoute, tenantsRoute, settingsRoute, graphRoute, importRoute, connectorsRoute]),
  callbackRoute
]);

void createRouteMask;
void createRouter;
