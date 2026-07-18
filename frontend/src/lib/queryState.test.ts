import { describe, expect, it } from "vitest";
import { MCPError, UnauthorizedError } from "@/lib/mcp";
import { isAuthError, isRenderableQueryError, queryViewState } from "@/lib/queryState";

/**
 * The dashboards must tell a genuine empty result apart from a backend failure.
 * A 500 is a renderable error (→ error/retry state); a 401 is NOT (a login
 * redirect is already in flight, so we keep the calm loading affordance).
 */
describe("query error classification", () => {
  it("treats a 401 (UnauthorizedError) as an auth error, not a renderable one", () => {
    const err = new UnauthorizedError();
    expect(isAuthError(err)).toBe(true);
    expect(isRenderableQueryError({ isError: true, error: err })).toBe(false);
    // A 401 keeps the UI in a loading posture while the redirect happens.
    expect(queryViewState({ isError: true, error: err })).toBe("loading");
  });

  it("treats a simulated 500 as a renderable error", () => {
    const err = new MCPError(500, "MCP request failed with HTTP 500");
    expect(isAuthError(err)).toBe(false);
    expect(isRenderableQueryError({ isError: true, error: err })).toBe(true);
    expect(queryViewState({ isError: true, error: err })).toBe("error");
  });

  it("does not flag a successful-but-empty query as an error", () => {
    expect(isRenderableQueryError({ isError: false, error: null })).toBe(false);
    expect(queryViewState({ isError: false, isPending: false })).toBe("ready");
    expect(queryViewState({ isLoading: true })).toBe("loading");
    expect(queryViewState({ isPending: true })).toBe("loading");
  });
});
