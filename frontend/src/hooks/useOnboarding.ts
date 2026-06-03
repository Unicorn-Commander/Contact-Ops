import { useEffect, useRef, useState } from "react";
import { useAuth } from "react-oidc-context";
import { env } from "@/lib/env";

/**
 * First-login auto-onboarding (Phase 8 — public onboarding launch).
 *
 * A brand-new user authenticates through Keycloak but their token carries NO
 * `tenant_id` claim yet — they have no personal workspace. This hook detects
 * that, calls the backend ONCE to provision one, then silently re-auths so the
 * refreshed token carries the new `tenant_id` and the app can proceed.
 *
 * Backend contract:
 *   POST {mcpBaseUrl}/api/auth/onboard   (Bearer access token, no body)
 *     200  → personal workspace provisioned + KC `tenant_id` attribute set
 *     503  → onboarding not configured on this deployment
 *     4xx/5xx → other failure
 *
 * IDEMPOTENCY / NO-LOOP (the load-bearing invariant):
 *   - We only onboard when `tenant_id` is GENUINELY absent (same claim-name
 *     fallbacks as `useActiveTenantId` in useMcp.ts: tenant_id → tenant →
 *     active_tenant_id).
 *   - After the silent re-auth the token should carry the claim, so the gate
 *     condition flips false and this never re-fires.
 *   - A `ranRef` guards against React Strict-Mode double-invoke and any
 *     transient re-render before the new token lands. The effect also bails if
 *     a run is already in flight or already completed.
 *   - The post-onboard silent re-auth MIRRORS `useSwitchWorkspace`:
 *     `forceIframeAuth: true` routes through the AUTHORIZE endpoint with
 *     prompt=none so Keycloak re-runs its protocol mappers against the
 *     now-updated user record and re-mints the claim (the refresh_token grant
 *     would reuse the cached, claim-less token).
 */

export type OnboardingPhase =
  | "idle" // no token / not authenticated yet, or tenant already present
  | "onboarding" // POST /api/auth/onboard + silent re-auth in flight
  | "done" // tenant_id present (either never-missing, or freshly provisioned)
  | "unavailable" // 503: onboarding not configured on this deployment
  | "error"; // network / non-503 failure — surfaced, not retried in a loop

export type OnboardingState = {
  phase: OnboardingPhase;
  /** True while we're actively provisioning — drives the "Setting up…" screen. */
  isOnboarding: boolean;
  /** True once it's safe to render the app (tenant present or onboarding skipped). */
  isReady: boolean;
  /** Human-readable message for the unavailable/error states. */
  message?: string;
  /** Manual retry for the error state (NOT auto-invoked, so no loop). */
  retry: () => void;
};

/** Read the workspace claim with the same fallbacks as `useActiveTenantId`. */
function readTenantClaim(profile: Record<string, unknown> | undefined): string | undefined {
  const claim = profile?.["tenant_id"] ?? profile?.["tenant"] ?? profile?.["active_tenant_id"];
  return typeof claim === "string" && claim.length > 0 ? claim : undefined;
}

export function useOnboarding(): OnboardingState {
  const auth = useAuth();
  const [phase, setPhase] = useState<OnboardingPhase>("idle");
  const [message, setMessage] = useState<string | undefined>(undefined);
  // Guards a single onboard attempt for this session. Set the instant a run
  // starts (synchronously, before any await) so a Strict-Mode double-mount or a
  // re-render mid-flight cannot fire a second POST.
  const ranRef = useRef(false);
  const [retryToken, setRetryToken] = useState(0);

  const isAuthenticated = auth.isAuthenticated;
  const profile = auth.user?.profile as Record<string, unknown> | undefined;
  const tenantId = readTenantClaim(profile);
  const hasTenant = Boolean(tenantId);

  useEffect(() => {
    // Not signed in yet, or still loading — nothing to do; keep phase idle.
    if (!isAuthenticated || !auth.user) return;

    // Tenant already present: never-missing OR the silent re-auth landed.
    // Flipping to "done" here is what makes the post-onboard state terminal,
    // so the POST cannot re-fire.
    if (hasTenant) {
      setPhase("done");
      return;
    }

    // tenant_id genuinely absent → onboard exactly once.
    if (ranRef.current) return;
    ranRef.current = true;
    setPhase("onboarding");
    setMessage(undefined);

    let cancelled = false;

    void (async () => {
      const token = auth.user?.access_token ?? "";
      try {
        // Bearer-only, matching lib/mcp.ts + useSwitchWorkspace: NO
        // `credentials: "include"`. The endpoint sets no cookie and sending
        // credentials would force a stricter CORS contract.
        const res = await fetch(`${env.mcpBaseUrl}/api/auth/onboard`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`
          }
        });

        if (res.status === 503) {
          if (cancelled) return;
          setMessage(
            "Workspace setup isn't available on this deployment yet. Please contact your administrator."
          );
          setPhase("unavailable");
          return;
        }
        if (res.status === 401) {
          // Bearer died between login and this call — bounce to a fresh login
          // rather than spin. (Mirrors the dead-bearer branch in useMcp.ts.)
          await auth.signinRedirect();
          return;
        }
        if (!res.ok) {
          throw new Error(`onboard failed: HTTP ${res.status}`);
        }

        // Provisioned. Re-mint the token so it carries the new tenant_id.
        // `forceIframeAuth: true` → AUTHORIZE + prompt=none so Keycloak re-runs
        // its mappers against the now-updated user (see useSwitchWorkspace).
        const newUser = await auth.signinSilent({ forceIframeAuth: true });

        if (cancelled) return;

        // Assert the claim actually re-minted. If the mapper/session cache
        // hasn't caught up, the gate would otherwise re-trigger — so surface it
        // as an error (manual retry) instead of looping. On success the
        // top-level `hasTenant` branch will flip us to "done" on the re-render
        // the new token triggers; we don't set "done" here to keep one source
        // of truth for the terminal state.
        const newClaim = readTenantClaim(newUser?.profile as Record<string, unknown> | undefined);
        if (!newClaim) {
          setMessage("Your workspace was created but didn't activate. Please try again.");
          setPhase("error");
          return;
        }
        // tenant present now — leave phase as-is; the hasTenant effect run
        // (fired by USER_LOADED from the silent re-auth) sets "done".
      } catch (err) {
        if (cancelled) return;
        setMessage(err instanceof Error ? err.message : "Workspace setup failed.");
        setPhase("error");
      }
    })();

    return () => {
      cancelled = true;
    };
    // retryToken is included so a manual retry re-runs this effect after we've
    // reset ranRef in `retry()`.
  }, [isAuthenticated, hasTenant, auth, retryToken]);

  return {
    phase,
    isOnboarding: phase === "onboarding",
    // App may render once a workspace exists. The unavailable/error states are
    // surfaced by AuthGate as their own screens (NOT counted as ready) so we
    // never drop a tenant-less user into a broken app.
    isReady: phase === "done",
    message,
    retry: () => {
      ranRef.current = false;
      setMessage(undefined);
      setPhase("onboarding");
      setRetryToken((n) => n + 1);
    }
  };
}
