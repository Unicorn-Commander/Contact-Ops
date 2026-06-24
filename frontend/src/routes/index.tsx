import { lazy, Suspense, useEffect } from "react";
import { createRootRoute, createRoute, createRouteMask, createRouter, Outlet, useNavigate } from "@tanstack/react-router";
import { useAuth } from "react-oidc-context";
import { AlertTriangle, Compass, Network, Sparkles } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { useOnboarding } from "@/hooks/useOnboarding";
import { useSignupConfig, type SignupConfig } from "@/hooks/useSignupConfig";
import { useConsent } from "@/lib/consent";
import { identifyPosthog } from "@/lib/analytics/posthog";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/design-system/Spinner";
import { AmbientGraph } from "@/design-system/v2/AmbientGraph";
import { ActionEventRow } from "@/components/domain/Lists";
import { DashboardRoute } from "@/routes/pages/Dashboard";
import { PeopleRoute, PersonDetailRoute } from "@/routes/pages/People";
import { OrgsRoute, OrgDetailRoute } from "@/routes/pages/Orgs";
import { TagsRoute } from "@/routes/pages/Tags";
import { DataQualityRoute } from "@/routes/pages/DataQuality";
import { TenantsRoute } from "@/routes/pages/Tenants";
import { AgentsRoute } from "@/routes/pages/Agents";
import { SettingsRoute } from "@/routes/pages/Settings";
import { ImportRoute } from "@/routes/pages/Import";
import { ConnectorsRoute } from "@/routes/pages/Connectors";
import { HelpConnectorsRoute } from "@/routes/pages/HelpConnectors";

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

// Design Language v2 showcase — a bolder "near-future" visual direction proposed
// for owner approval before any real-page refactor. Showcase-only; imports the
// additive tokens-v2.css and the v2/* components and touches nothing in the app.
const DesignSystemV2Route = import.meta.env.DEV
  ? lazy(() => import("@/design-system/ShowcaseV2").then((m) => ({ default: m.ShowcaseV2 })))
  : null;

// Shell v2 chrome preview — auth-free twin of the AppShell header with the
// first-class "Ask AI ⌘J" pill, the docked "Ask the Analyst" launcher, and the
// v2 glass tenant chip, plus a dark/light toggle. Lets the orchestrator
// screenshot the Phase-3 shell restyle without a Keycloak session. DEV only.
const ShellV2PreviewRoute = import.meta.env.DEV
  ? lazy(() => import("@/design-system/ShellPreviewV2").then((m) => ({ default: m.ShellPreviewV2 })))
  : null;

// Dashboard command-center hero — auth-free preview of the REAL v2 hero
// (CommandCenterHero/HeroStat from the live Dashboard) with sample counts, so
// the hero can be reviewed without a Keycloak session or MCP backend. DEV only.
const DashboardHeroPreviewRoute = import.meta.env.DEV
  ? lazy(() =>
      import("@/design-system/DashboardHeroPreview").then((m) => ({ default: m.DashboardHeroPreview }))
    )
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

// Connectors-help auth-free preview — mirrors ConnectorsPreviewRoute so the
// orchestrator can wire the Showcase tab + screenshot harness on the same
// pattern. Page is light (no graph/canvas) so eager-loading would be fine,
// but staying lazy keeps parity with the other previews.
const HelpConnectorsPreviewRoute = import.meta.env.DEV
  ? lazy(() =>
      import("@/design-system/HelpConnectorsPreview").then((m) => ({
        default: m.HelpConnectorsPreview
      }))
    )
  : null;

// iCloud modal preview — mounts the *real* IcloudConnectModal so the
// disclosure + decoded error banner are screenshottable without auth. Used
// by the connectors-help screenshot harness for the modal captures.
const IcloudModalPreviewRoute = import.meta.env.DEV
  ? lazy(() =>
      import("@/design-system/IcloudModalPreview").then((m) => ({
        default: m.IcloudModalPreview
      }))
    )
  : null;

// Bulk-approve preview — quality-filter chips + bulk action row rendered with
// a synthetic 839-proposal corpus so the screenshot harness can capture the
// surface without a live MCP backend or auth.
const BulkApprovePreviewRoute = import.meta.env.DEV
  ? lazy(() =>
      import("@/design-system/BulkApprovePreview").then((m) => ({
        default: m.BulkApprovePreview
      }))
    )
  : null;

// Personal Access Tokens preview — empty / issued / populated / help expanded
// scenarios of the Settings PAT card. Network-free presentational mirror of
// `PersonalAccessTokensSection`.
const PATPreviewRoute = import.meta.env.DEV
  ? lazy(() =>
      import("@/design-system/PATPreview").then((m) => ({
        default: m.PATPreview
      }))
    )
  : null;

// Review Queue v2 preview — auth-free preview of the REAL Review-Queue
// components (ClusterCard / ProposalRow / ConflictBanner / bulk surfaces) over
// a representative sample so the redesign can be screenshot-gated without a
// Keycloak session or the MCP backend. DEV only.
const ReviewQueuePreviewRoute = import.meta.env.DEV
  ? lazy(() =>
      import("@/design-system/ReviewQueuePreview").then((m) => ({
        default: m.ReviewQueuePreview
      }))
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

function LazyDesignSystemV2Route() {
  if (!DesignSystemV2Route) return null;
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <Skeleton className="h-32 w-72" />
        </div>
      }
    >
      <DesignSystemV2Route />
    </Suspense>
  );
}

function LazyShellV2PreviewRoute() {
  if (!ShellV2PreviewRoute) return null;
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <Skeleton className="h-32 w-72" />
        </div>
      }
    >
      <ShellV2PreviewRoute />
    </Suspense>
  );
}

function LazyDashboardHeroPreviewRoute() {
  if (!DashboardHeroPreviewRoute) return null;
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <Skeleton className="h-32 w-72" />
        </div>
      }
    >
      <DashboardHeroPreviewRoute />
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

function LazyHelpConnectorsPreviewRoute() {
  if (!HelpConnectorsPreviewRoute) return null;
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
          <HelpConnectorsPreviewRoute />
        </div>
      </div>
    </Suspense>
  );
}

function LazyIcloudModalPreviewRoute() {
  if (!IcloudModalPreviewRoute) return null;
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
          <IcloudModalPreviewRoute />
        </div>
      </div>
    </Suspense>
  );
}

function LazyBulkApprovePreviewRoute() {
  if (!BulkApprovePreviewRoute) return null;
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
          <BulkApprovePreviewRoute />
        </div>
      </div>
    </Suspense>
  );
}

function LazyPATPreviewRoute() {
  if (!PATPreviewRoute) return null;
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
          <PATPreviewRoute />
        </div>
      </div>
    </Suspense>
  );
}

function LazyReviewQueuePreviewRoute() {
  if (!ReviewQueuePreviewRoute) return null;
  // The preview renders its own full-screen <main> (header + padding), so this
  // wrapper is a bare Suspense boundary — same shape as LazyDesignSystemV2Route.
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <Skeleton className="h-32 w-72" />
        </div>
      }
    >
      <ReviewQueuePreviewRoute />
    </Suspense>
  );
}

function AuthGate() {
  const auth = useAuth();
  // Pre-auth: whether to surface a signup CTA on the login screen (backend-driven).
  const signupConfig = useSignupConfig();
  const { consent } = useConsent();

  // Identify product analytics by the OPAQUE uc_uid (never name/email), grouped
  // by tenant. No-op unless PostHog is configured AND product-consent is given.
  useEffect(() => {
    if (!auth.isAuthenticated || !consent.product) return;
    const profile = auth.user?.profile as Record<string, unknown> | undefined;
    const sub = typeof profile?.sub === "string" ? profile.sub : undefined;
    const tenantId = typeof profile?.tenant_id === "string" ? profile.tenant_id : undefined;
    if (sub) identifyPosthog(sub, tenantId);
  }, [auth.isAuthenticated, consent.product, auth.user]);
  // First-login auto-onboarding: provisions a personal workspace for a
  // brand-new user (token has no tenant_id), then silently re-auths so the
  // refreshed token carries it. Strictly idempotent — see useOnboarding. The
  // hook returns phase "idle" until auth resolves, so calling it
  // unconditionally is safe.
  const onboarding = useOnboarding();

  if (auth.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <Spinner label="Checking your session…" size="lg" />
      </div>
    );
  }
  if (!auth.isAuthenticated) {
    return (
      <LoginHero
        onSignIn={() => void auth.signinRedirect()}
        // Standalone mode: trigger Keycloak self-registration (KC 23+/26 maps the
        // OIDC `prompt=create` to its registration flow). The exact param may need
        // tuning to the realm's KC version when signup is enabled.
        onSignUp={() => void auth.signinRedirect({ extraQueryParams: { prompt: "create" } })}
        signupConfig={signupConfig.data}
      />
    );
  }

  // Brand-new user → provisioning their workspace (POST /api/auth/onboard +
  // silent re-auth). Brief, self-resolving state.
  if (onboarding.isOnboarding) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <Spinner label="Setting up your workspace…" size="lg" />
      </div>
    );
  }
  // Onboarding not configured (503) or it failed — surface a message instead of
  // crashing or looping. Sign-out is the only safe action for "unavailable";
  // "error" also offers a manual retry.
  if (onboarding.phase === "unavailable" || onboarding.phase === "error") {
    return (
      <OnboardingNotice
        title={
          onboarding.phase === "unavailable"
            ? "Workspace setup unavailable"
            : "We couldn't finish setting up your workspace"
        }
        message={onboarding.message}
        onRetry={onboarding.phase === "error" ? onboarding.retry : undefined}
        onSignOut={() => void auth.signoutRedirect()}
      />
    );
  }

  return <AppShell />;
}

function OnboardingNotice({
  title,
  message,
  onRetry,
  onSignOut
}: {
  title: string;
  message?: string;
  onRetry?: () => void;
  onSignOut: () => void;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-lg text-center">
        <CardContent className="flex flex-col items-center gap-4 py-16">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <AlertTriangle className="h-7 w-7" strokeWidth={1.5} />
          </div>
          <div className="space-y-1.5">
            <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
            <p className="mx-auto max-w-sm text-sm text-muted-foreground">
              {message ?? "Please try again or contact your administrator."}
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-2">
            {onRetry ? <Button onClick={onRetry}>Try again</Button> : null}
            <Button variant="ghost" onClick={onSignOut}>
              Sign out
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Design Language v2 Login hero (promoted from the /design-system/v2 showcase).
 * Cinematic brand surface: an ambient glowing relationship graph + radial bloom
 * behind a bold gradient headline + gradient wordmark, with a layered-glass
 * sign-in card.
 *
 * Auth is UNCHANGED — `onSignIn` is the real react-oidc-context
 * `auth.signinRedirect()` passed from AuthGate; the CTA only restyles the
 * existing trigger. Every animation (graph drift, bloom pulse, wordmark pan) is
 * gated behind prefers-reduced-motion via tokens-v2.css; the AmbientGraph paints
 * a single static frame in that mode. All text sits on the .co-v2-stage-content
 * layer and carries its own AA contrast over the decorative graph.
 */
function LoginHero({
  onSignIn,
  onSignUp,
  signupConfig
}: {
  onSignIn: () => void;
  onSignUp: () => void;
  signupConfig?: SignupConfig;
}) {
  // Dormant by default: the signup CTA only appears when the backend reports
  // signup_enabled. "suite" → link out to the UC suite signup (it owns
  // accounts/subscriptions); "standalone" → trigger CO's own KC registration.
  const showSignup = signupConfig?.signup_enabled === true;
  const suiteMode = signupConfig?.signup_mode === "suite";
  const suiteUrl = signupConfig?.suite_signup_url ?? undefined;
  return (
    <div className="co-v2-ambient co-v2-vignette relative flex min-h-screen items-center overflow-hidden">
      <AmbientGraph nodeCount={30} className="opacity-90" />
      <div className="co-v2-stage-content mx-auto grid w-full max-w-[1400px] items-center gap-10 px-6 py-12 lg:grid-cols-2 lg:px-14 lg:py-16">
        {/* Left, the brand promise */}
        <div className="co-animate-fade-in max-w-xl">
          <img
            src="/brand/officer.png"
            alt="The Contact-Ops Connections Officer"
            className="mb-5 h-28 w-28"
            style={{ filter: "drop-shadow(0 0 34px rgba(25, 195, 125, 0.42))" }}
          />
          <span className="inline-flex items-center gap-2 rounded-full border border-[oklch(var(--co-v2-glass-hairline-strong))] bg-[oklch(var(--co-v2-glass-fill))] px-3 py-1 text-xs font-medium text-foreground backdrop-blur">
            <Network className="h-3.5 w-3.5 text-[oklch(var(--co-brand-300))]" strokeWidth={2} />
            Relationship intelligence you self-host
          </span>
          <h1 className="mt-6 text-4xl font-bold leading-[1.08] tracking-tight text-foreground lg:text-5xl">
            Every person.{" "}
            <span className="co-v2-wordmark co-v2-wordmark-animate">Every connection.</span>{" "}
            One source of truth.
          </h1>
          <p className="mt-5 max-w-md text-base leading-relaxed text-muted-foreground">
            Contact-Ops turns your people and organizations into a living knowledge graph you own,
            with AI agents that keep it clean, connected, and current. Part of the Unicorn Commander
            suite.
          </p>
          <ul className="mt-6 grid max-w-md gap-2.5 text-sm text-foreground">
            <li className="flex items-start gap-2.5">
              <Network className="mt-0.5 h-4 w-4 shrink-0 text-[oklch(var(--co-brand-500))]" strokeWidth={2} />
              A relationship graph, not a flat address book.
            </li>
            <li className="flex items-start gap-2.5">
              <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-[oklch(var(--co-brand-500))]" strokeWidth={2} />
              An AI analyst that reasons over your contacts.
            </li>
            <li className="flex items-start gap-2.5">
              <Compass className="mt-0.5 h-4 w-4 shrink-0 text-[oklch(var(--co-brand-500))]" strokeWidth={2} />
              Self-hosted: your data, on your own infrastructure.
            </li>
          </ul>
        </div>

        {/* Right — the sign-in glass card, lifted on its own bloom */}
        <div className="co-animate-fade-in relative mx-auto w-full max-w-md">
          <span
            className="co-v2-bloom co-v2-bloom-pulse"
            style={{ inset: "-12% -8% auto -8%", height: "60%" }}
            aria-hidden="true"
          />
          <div className="co-v2-glass co-v2-glass-edge relative p-8 text-center">
            <img
              src="/brand/app-icon.svg"
              alt=""
              className="co-v2-glow-strong mx-auto mb-6 h-16 w-16 rounded-2xl"
              aria-hidden="true"
            />
            <h2 className="co-v2-wordmark text-3xl font-bold tracking-tight">Contact-Ops</h2>
            <p className="mx-auto mt-2.5 max-w-xs text-sm leading-relaxed text-muted-foreground">
              Sign in with Unicorn Commander to manage people, organizations, and the agents that keep
              them clean.
            </p>
            <Button
              className="co-v2-hover-bloom co-v2-glow-strong mt-8 h-12 w-full text-sm"
              variant="gradient"
              onClick={onSignIn}
            >
              <Sparkles className="h-4 w-4" strokeWidth={2} />
              Sign in with Unicorn Commander
            </Button>
            <p className="mt-4 text-xs text-muted-foreground">Secured by Unicorn Commander SSO</p>

            {showSignup ? (
              <div className="mt-6 border-t border-[oklch(var(--co-v2-glass-hairline-strong))] pt-5">
                <p className="text-xs text-muted-foreground">New here?</p>
                {suiteMode ? (
                  <a
                    href={suiteUrl ?? "#"}
                    className="text-link hover:underline mt-1.5 inline-block text-sm font-medium"
                    {...(suiteUrl ? {} : { "aria-disabled": true })}
                  >
                    Create a Unicorn Commander account →
                  </a>
                ) : (
                  <Button
                    variant="outline"
                    className="mt-2 h-11 w-full text-sm"
                    onClick={onSignUp}
                  >
                    Create your account
                  </Button>
                )}
              </div>
            ) : null}
          </div>
          <p className="mt-4 text-center text-xs text-muted-foreground">by Magic Unicorn</p>
          <p className="mt-2 text-center text-[11px] text-muted-foreground">
            <a href="/legal/terms.html" className="text-link hover:underline">Terms</a>
            {" · "}
            <a href="/legal/privacy.html" className="text-link hover:underline">Privacy</a>
            {" · "}
            <a href="/legal/dpa.html" className="text-link hover:underline">DPA</a>
          </p>
        </div>
      </div>
    </div>
  );
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
const dataQualityRoute = createRoute({ getParentRoute: () => appRoute, path: "/data-quality", component: DataQualityRoute });
const reviewRoute = createRoute({ getParentRoute: () => appRoute, path: "/review", component: LazyInboxRoute });
const inboxRedirectRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/inbox",
  beforeLoad: () => {
    throw { replace: true, to: "/review" };
  }
});
const tenantsRoute = createRoute({ getParentRoute: () => appRoute, path: "/tenants", component: TenantsRoute });
const agentsRoute = createRoute({ getParentRoute: () => appRoute, path: "/agents", component: AgentsRoute });
const settingsRoute = createRoute({ getParentRoute: () => appRoute, path: "/settings", component: SettingsRoute });
const graphRoute = createRoute({ getParentRoute: () => appRoute, path: "/graph", component: LazyGraphRoute });
const importRoute = createRoute({ getParentRoute: () => appRoute, path: "/import", component: ImportRoute });
const connectorsRoute = createRoute({ getParentRoute: () => appRoute, path: "/connectors", component: ConnectorsRoute });
const helpConnectorsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/help/connectors",
  component: HelpConnectorsRoute
});
const designSystemRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system",
      component: LazyDesignSystemRoute
    })
  : null;
const designSystemV2Route = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/v2",
      component: LazyDesignSystemV2Route
    })
  : null;
const designSystemShellV2Route = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/shell-v2",
      component: LazyShellV2PreviewRoute
    })
  : null;
const designSystemDashboardHeroRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/dashboard-hero",
      component: LazyDashboardHeroPreviewRoute
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
const designSystemHelpConnectorsRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/help-connectors",
      component: LazyHelpConnectorsPreviewRoute
    })
  : null;
const designSystemIcloudModalRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/icloud-modal",
      component: LazyIcloudModalPreviewRoute
    })
  : null;
const designSystemBulkApproveRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/bulk-approve",
      component: LazyBulkApprovePreviewRoute
    })
  : null;
const designSystemPATsRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/pats",
      component: LazyPATPreviewRoute
    })
  : null;
const designSystemReviewQueueRoute = import.meta.env.DEV
  ? createRoute({
      getParentRoute: () => rootRoute,
      path: "/design-system/review-queue",
      component: LazyReviewQueuePreviewRoute
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
      // viewTransition:false: this redirect off the (invisible) spinner is the
      // navigation most likely to collide with an in-flight transition during the
      // login burst, and a cross-fade here is pointless. Opt it out at the source;
      // defaultViewTransition stays on for real user navigations.
      void navigate({ to: "/", replace: true, viewTransition: false });
    }
  }, [auth.isLoading, auth.isAuthenticated, navigate]);
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Spinner label="Signing you in…" size="lg" />
    </div>
  );
}

export const routeTree = rootRoute.addChildren([
  ...(designSystemV2Route ? [designSystemV2Route] : []),
  ...(designSystemShellV2Route ? [designSystemShellV2Route] : []),
  ...(designSystemDashboardHeroRoute ? [designSystemDashboardHeroRoute] : []),
  ...(designSystemAIAnalystRoute ? [designSystemAIAnalystRoute] : []),
  ...(designSystemNotesRoute ? [designSystemNotesRoute] : []),
  ...(designSystemImportRoute ? [designSystemImportRoute] : []),
  ...(designSystemConnectorsRoute ? [designSystemConnectorsRoute] : []),
  ...(designSystemHelpConnectorsRoute ? [designSystemHelpConnectorsRoute] : []),
  ...(designSystemIcloudModalRoute ? [designSystemIcloudModalRoute] : []),
  ...(designSystemBulkApproveRoute ? [designSystemBulkApproveRoute] : []),
  ...(designSystemPATsRoute ? [designSystemPATsRoute] : []),
  ...(designSystemReviewQueueRoute ? [designSystemReviewQueueRoute] : []),
  ...(designSystemRoute ? [designSystemRoute] : []),
  appRoute.addChildren([indexRoute, peopleRoute, personRoute, personGraphRoute, orgsRoute, orgRoute, tagsRoute, dataQualityRoute, reviewRoute, inboxRedirectRoute, tenantsRoute, agentsRoute, settingsRoute, graphRoute, importRoute, connectorsRoute, helpConnectorsRoute]),
  callbackRoute
]);

void createRouteMask;
void createRouter;
