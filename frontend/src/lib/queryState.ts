/**
 * Query view-state helpers.
 *
 * The dashboards must distinguish THREE outcomes of a data query:
 *   - loading          → skeletons
 *   - genuine empty     → the friendly "nothing here yet" empty state
 *   - non-401 failure   → an ERROR/RETRY state (NOT the empty state)
 *
 * Historically a backend 500 rendered identically to a real zero-result — a
 * failing inbox query painted "Review queue is clear" / "inbox zero", which
 * silently lies to the user. These helpers make the distinction explicit and
 * are unit-tested (see queryState.test.ts).
 *
 * 401 (UnauthorizedError) is deliberately NOT treated as a render-able error:
 * the data hooks fire `auth.signinRedirect()` on 401, so a login redirect is
 * already in flight. Painting a red "Something went wrong — Retry" box during
 * that redirect would be wrong; we keep showing the calm loading affordance
 * until the redirect lands.
 */
import { UnauthorizedError } from "@/lib/mcp";

/** True when the error is an auth (401) failure that triggers a login redirect. */
export function isAuthError(error: unknown): boolean {
  return error instanceof UnauthorizedError;
}

/**
 * A non-401 query failure that SHOULD surface an error/retry state.
 *
 * Returns false for a 401 (redirect in flight) and for the no-error case.
 */
export function isRenderableQueryError(query: {
  isError?: boolean;
  error?: unknown;
}): boolean {
  return Boolean(query.isError) && !isAuthError(query.error);
}

export type QueryViewState = "loading" | "error" | "ready";

/**
 * Collapse a React Query result into the three states the dashboards branch on.
 *
 * - A 401 error resolves to "loading" (a redirect is happening; show skeletons).
 * - Any other error resolves to "error".
 * - Otherwise "ready" (the caller then decides empty-vs-list from the data).
 *
 * Accepts both plain queries (`isLoading`) and infinite/pending queries
 * (`isPending`); either loading signal counts.
 */
export function queryViewState(query: {
  isLoading?: boolean;
  isPending?: boolean;
  isError?: boolean;
  error?: unknown;
}): QueryViewState {
  if (query.isError) {
    return isAuthError(query.error) ? "loading" : "error";
  }
  if (query.isLoading || query.isPending) return "loading";
  return "ready";
}
