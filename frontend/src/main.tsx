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

// Native View Transitions (enabled via the router's defaultViewTransition) throw a
// benign DOMException when an in-flight transition is superseded by a rapid follow-up
// navigation. That is expected per the View Transitions spec, and it happens during
// auth churn: the cold load, the OIDC /auth/callback, and the post-callback redirect
// to "/" all settle in quick succession, so each can abort the prior transition. The
// page is unaffected (the cross-fade just resolves instantly), but the abort surfaces
// as an uncaught rejection/error that pollutes the console. Swallow ONLY this exact
// benign case, by DOMException name + a transition-scoped message, so it can never
// mask an unrelated InvalidStateError (IndexedDB, WebRTC, media) or a real app fault.
// The message text differs between Chrome ("Transition was aborted because of invalid
// state") and the spec ("...skipped because document visibility state is hidden") and
// has drifted across versions, so the predicate matches all known phrasings.
function isBenignAbortedViewTransition(value: unknown): boolean {
  return (
    value instanceof DOMException &&
    value.name === "InvalidStateError" &&
    /view transition|transition was aborted|visibility state is hidden/i.test(value.message)
  );
}
window.addEventListener("unhandledrejection", (event) => {
  if (isBenignAbortedViewTransition(event.reason)) event.preventDefault();
});
window.addEventListener("error", (event) => {
  if (isBenignAbortedViewTransition(event.error)) event.preventDefault();
});

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
