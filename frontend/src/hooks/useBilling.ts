/**
 * Billing queries + mutations (Phase: billing-ops-center).
 *
 * Thin React Query layer over `@/lib/billing`, following the same shape as
 * `usePersonalAccessTokens.ts`:
 *   - 401 from any call triggers a silent re-auth (the token expired).
 *   - The status query is user-scoped (`userKey.billingStatus`), so it survives
 *     a workspace switch's cache clear — subscription is per-user, not per-tenant.
 *   - The checkout mutation toasts only on a *real* failure; an unconfigured
 *     billing backend (503 -> `BillingUnconfiguredError`) is expected and is
 *     handled by the CTA hiding/disabling itself, not by an error toast.
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { toast } from "sonner";
import {
  BillingUnconfiguredError,
  createCheckout,
  getBillingStatus,
  type BillingStatus,
  type CheckoutArgs,
  type CheckoutResponse
} from "@/lib/billing";
import { MCPError, UnauthorizedError } from "@/lib/mcp";
import { userKey } from "@/lib/queryKeys";

/**
 * Current subscription status. Always enabled once a token exists; the backend
 * is INERT-safe so this is cheap even when billing is unconfigured. Stale-time
 * is generous — tier rarely changes within a session, and the Settings card
 * invalidates on demand after a checkout round-trip.
 */
export function useBillingStatus() {
  const auth = useAuth();
  const token = auth.user?.access_token;
  return useQuery<BillingStatus>({
    queryKey: userKey.billingStatus,
    enabled: Boolean(token),
    staleTime: 60_000,
    queryFn: async () => {
      try {
        return await getBillingStatus(token ?? "");
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    }
  });
}

/**
 * Start a checkout and hand back the Stripe URL. On success the caller redirects
 * the browser; this hook deliberately does NOT navigate, so the consumer owns
 * the redirect (and we never redirect out from under a failed call).
 *
 * `BillingUnconfiguredError` is swallowed by the toast path — the CTA should
 * already be hidden in that state, so it would only appear in a race; we still
 * rethrow so the consumer's `catch` can no-op cleanly.
 */
export function useCheckoutMutation() {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";

  return useMutation<CheckoutResponse, Error, CheckoutArgs>({
    mutationFn: async (args) => {
      try {
        return await createCheckout(args, token);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onError: (err) => {
      if (err instanceof BillingUnconfiguredError || err instanceof UnauthorizedError) return;
      const msg = err instanceof MCPError ? err.message : err.message;
      toast.error(`Could not start checkout: ${msg}`);
    }
  });
}
