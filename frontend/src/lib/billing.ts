/**
 * Billing API client (Phase: billing-ops-center).
 *
 * Contact-Ops does NOT run its own Stripe — the backend (`api/billing.py`) is a
 * thin proxy to the central Ops-Center billing brain. These three REST calls
 * mirror that proxy verbatim:
 *
 *   GET  /api/v1/billing/status   -> { tier, status?, plan?, configured, portal_url? }
 *   GET  /api/v1/billing/plans    -> { plans, configured? } | Ops-Center body
 *   POST /api/v1/billing/checkout -> { checkout_url, session_id? }
 *
 * Transport notes (matching the rest of the app):
 *   - Same origin as the MCP/chat/auth routers, i.e. `env.mcpBaseUrl` — the same
 *     base `sseStream` and `useSwitchWorkspace` already POST to.
 *   - Bearer-only auth (the OIDC access token); NO `credentials: "include"`,
 *     so the cross-origin call stays compatible with a wildcard Allow-Origin
 *     (see the note in `useSwitchWorkspace`).
 *   - 401 -> `UnauthorizedError` so callers can trigger a silent re-auth, exactly
 *     like `mcpCall`. Other non-OK statuses -> `MCPError` carrying the HTTP code.
 *
 * INERT-when-unconfigured contract (load-bearing for the UI):
 *   - `GET /status` never 5xx's; it degrades to `{ tier: "free", configured: false }`.
 *   - `POST /checkout` returns **503** when billing is not configured. We surface
 *     that as `BillingUnconfiguredError` so the CTA can disable itself gracefully
 *     rather than showing a generic failure toast.
 */
import { env } from "@/lib/env";
import { MCPError, UnauthorizedError } from "@/lib/mcp";

/**
 * Billing is not wired up yet (Ops-Center URL / service key unset). Checkout
 * returns 503 in that state; the UI treats this as "hide/disable the CTA",
 * not as an error to shout about.
 */
export class BillingUnconfiguredError extends Error {
  constructor() {
    super("Billing is not configured");
    this.name = "BillingUnconfiguredError";
  }
}

/**
 * Normalized status from `GET /api/v1/billing/status`.
 *
 * `tier` is Ops-Center's string code (e.g. "free", "pro") — NOT the numeric
 * trust `Tier` used by the inbox. `configured` gates whether any upgrade /
 * manage affordance should render at all.
 */
export type BillingStatus = {
  tier: string;
  status?: string | null;
  plan?: string | null;
  configured: boolean;
  /** Stripe customer-portal link; present only for an active paid subscription. */
  portal_url?: string | null;
};

/** A single plan from `GET /api/v1/billing/plans` (Ops-Center's shape, kept loose). */
export type BillingPlan = {
  /** The code passed back to `POST /checkout` as `plan_code`. */
  plan_code?: string;
  code?: string;
  name?: string;
  description?: string | null;
  /** Cents or a pre-formatted string, depending on the Ops-Center path. */
  price?: number | string | null;
  currency?: string | null;
  interval?: string | null;
  tier?: string | null;
  [key: string]: unknown;
};

export type BillingPlansResult = {
  plans: BillingPlan[];
  configured?: boolean;
};

export type CheckoutResponse = {
  checkout_url: string;
  session_id?: string | null;
};

export type CheckoutArgs = {
  plan_code: string;
  /** Where Stripe sends the browser on success / cancel. Defaults to this app. */
  success_url?: string;
  cancel_url?: string;
};

/** Shared fetch wrapper: bearer auth + the app's 401/own-error mapping. */
async function billingFetch(
  path: string,
  token: string,
  init?: RequestInit
): Promise<Response> {
  const res = await fetch(`${env.mcpBaseUrl}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${token}`,
      ...(init?.headers ?? {})
    }
  });
  if (res.status === 401) throw new UnauthorizedError();
  return res;
}

/**
 * Read the caller's subscription status. The backend guarantees this never
 * 5xx's, but we still defend against transport / parse faults by degrading to
 * the same safe `configured: false` shape rather than throwing — a billing read
 * must never break the shell that renders the tier badge.
 */
export async function getBillingStatus(token: string): Promise<BillingStatus> {
  const safe: BillingStatus = { tier: "free", configured: false };
  try {
    const res = await billingFetch("/api/v1/billing/status", token);
    if (!res.ok) return safe;
    const data = (await res.json()) as Partial<BillingStatus> | null;
    if (!data || typeof data !== "object") return safe;
    return {
      tier: typeof data.tier === "string" && data.tier ? data.tier : "free",
      status: data.status ?? null,
      plan: data.plan ?? null,
      configured: data.configured === true,
      portal_url: data.portal_url ?? null
    };
  } catch (err) {
    // Re-auth must still propagate; everything else degrades to the safe shape.
    if (err instanceof UnauthorizedError) throw err;
    return safe;
  }
}

/** List available plans. Returns `{ plans: [], configured: false }` when inert. */
export async function getBillingPlans(token: string): Promise<BillingPlansResult> {
  const res = await billingFetch("/api/v1/billing/plans", token);
  if (!res.ok) throw new MCPError(res.status, `Billing plans request failed (HTTP ${res.status})`);
  const data = (await res.json()) as BillingPlansResult | BillingPlan[] | null;
  if (Array.isArray(data)) return { plans: data };
  if (data && Array.isArray(data.plans)) return { plans: data.plans, configured: data.configured };
  return { plans: [] };
}

/**
 * Create an Ops-Center checkout session and return the Stripe URL.
 *
 * Defaults the post-payment redirects to the current app origin so the user
 * lands back in Contact-Ops. Maps the backend's 503 (billing unconfigured) to
 * `BillingUnconfiguredError` so the caller can disable the CTA instead of
 * surfacing a hard error.
 */
export async function createCheckout(args: CheckoutArgs, token: string): Promise<CheckoutResponse> {
  const origin = window.location.origin;
  const body: CheckoutArgs = {
    plan_code: args.plan_code,
    success_url: args.success_url ?? `${origin}/settings?billing=success`,
    cancel_url: args.cancel_url ?? `${origin}/settings?billing=cancelled`
  };
  const res = await billingFetch("/api/v1/billing/checkout", token, {
    method: "POST",
    body: JSON.stringify(body)
  });
  if (res.status === 503) throw new BillingUnconfiguredError();
  if (!res.ok) throw new MCPError(res.status, `Checkout failed (HTTP ${res.status})`);
  const data = (await res.json()) as Partial<CheckoutResponse> | null;
  const url = data?.checkout_url;
  if (!url || typeof url !== "string") {
    throw new MCPError("NO_CHECKOUT_URL", "Checkout did not return a redirect URL");
  }
  return { checkout_url: url, session_id: data?.session_id ?? null };
}
