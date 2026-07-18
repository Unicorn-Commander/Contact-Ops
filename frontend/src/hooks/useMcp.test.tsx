import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

import { makeTestQueryClient, withQueryClient } from "@/test/utils";
import { tools, UnauthorizedError } from "@/lib/mcp";

// --- react-oidc-context is mocked so we control the token + spy on redirects.
const signinRedirect = vi.fn();
const mockAuth: {
  user: { access_token: string; profile: Record<string, unknown> } | undefined;
  signinRedirect: typeof signinRedirect;
} = {
  user: { access_token: "test-token", profile: { tenant_id: "tenant-1", name: "Tester" } },
  signinRedirect
};

vi.mock("react-oidc-context", () => ({
  useAuth: () => mockAuth
}));

describe("useMcp — 401 handling", () => {
  beforeEach(() => {
    signinRedirect.mockReset();
  });

  it("redirects to login when a data query throws UnauthorizedError (401)", async () => {
    // Simulate the MCP endpoint returning 401 for the dashboard's tools/list.
    vi.spyOn(tools, "list").mockRejectedValue(new UnauthorizedError());

    // Import lazily so the react-oidc-context mock is applied first.
    const { useToolsList } = await import("@/hooks/useMcp");

    const client = makeTestQueryClient();
    const { result } = renderHook(() => useToolsList(), {
      wrapper: withQueryClient(client)
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    // The money path: a 401 kicks off a login redirect exactly once.
    expect(signinRedirect).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBeInstanceOf(UnauthorizedError);
  });
});
