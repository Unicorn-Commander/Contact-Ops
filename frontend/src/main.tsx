import React from "react";
import ReactDOM from "react-dom/client";
import { AuthProvider } from "react-oidc-context";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { HotkeysProvider } from "react-hotkeys-hook";
import { MotionConfig } from "framer-motion";
import { Toaster } from "sonner";
import { oidcConfig } from "@/lib/auth";
import { routeTree } from "@/routes";
import { ConsentProvider } from "@/lib/consent";
import { ConsentBanner } from "@/components/ConsentBanner";
import "@/index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1
    }
  }
});

const router = createRouter({
  routeTree,
  context: { queryClient },
  // Native CSS View Transitions on every navigation (Baseline 2026; auto-degrades
  // to an instant swap where unsupported). The cross-fade is styled and
  // reduced-motion-gated in index.css. Unchanged regions (the app shell) animate
  // old==new pixels, so only the changed content visibly transitions.
  defaultViewTransition: true
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

// Manual, path-only pageview tracking (Umami auto-track is off so it never fires
// pre-consent or leaks query-string PII). No-op until Umami is configured + the
// user has consented (the loader injects the script only then).
import { trackPageview } from "@/lib/analytics/umami";
router.subscribe("onResolved", () => {
  trackPageview(window.location.pathname);
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider {...oidcConfig}>
      <QueryClientProvider client={queryClient}>
        <HotkeysProvider initiallyActiveScopes={["global"]}>
          {/* reducedMotion="user" makes every framer-motion animation honour the
              OS prefers-reduced-motion setting app-wide, in one place. */}
          <MotionConfig reducedMotion="user">
            <ConsentProvider>
              <Toaster
                position="bottom-right"
                richColors
                closeButton
                toastOptions={{
                  style: { fontFamily: "Inter, sans-serif" },
                }}
              />
              <RouterProvider router={router} />
              <ConsentBanner />
            </ConsentProvider>
          </MotionConfig>
        </HotkeysProvider>
      </QueryClientProvider>
    </AuthProvider>
  </React.StrictMode>
);
