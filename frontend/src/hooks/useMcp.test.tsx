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

// Build an unsigned JWT whose payload carries the given claims. The hook never
// verifies the signature — it only reads the (base64url) payload.
function jwtWith(payload: Record<string, unknown>): string {
  const b64 = (obj: Record<string, unknown>) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64({ alg: "none" })}.${b64(payload)}.`;
}

describe("useMcp — scope helper (useHasScope)", () => {
  beforeEach(() => {
    signinRedirect.mockReset();
  });

  it("reads a space-delimited scope claim from the ACCESS token", async () => {
    mockAuth.user = {
      access_token: jwtWith({ scope: "openid person:read identifier:verify" }),
      profile: { tenant_id: "tenant-1" }
    };
    const { useHasScope } = await import("@/hooks/useMcp");

    expect(renderHook(() => useHasScope("identifier:verify")).result.current).toBe(true);
    expect(renderHook(() => useHasScope("identifier:write")).result.current).toBe(false);
  });

  it("accepts an array-valued scope claim", async () => {
    mockAuth.user = {
      access_token: jwtWith({ scope: ["identifier:verify", "person:write"] }),
      profile: { tenant_id: "tenant-1" }
    };
    const { useHasScope } = await import("@/hooks/useMcp");

    expect(renderHook(() => useHasScope("person:write")).result.current).toBe(true);
  });

  it("returns false when the token is absent, malformed or carries no scope", async () => {
    const { useHasScope } = await import("@/hooks/useMcp");

    mockAuth.user = undefined;
    expect(renderHook(() => useHasScope("identifier:verify")).result.current).toBe(false);

    mockAuth.user = { access_token: "not-a-jwt", profile: {} };
    expect(renderHook(() => useHasScope("identifier:verify")).result.current).toBe(false);

    mockAuth.user = { access_token: jwtWith({ sub: "u1" }), profile: {} };
    expect(renderHook(() => useHasScope("identifier:verify")).result.current).toBe(false);

    // Restore the default used by the 401 test below.
    mockAuth.user = {
      access_token: "test-token",
      profile: { tenant_id: "tenant-1", name: "Tester" }
    };
  });
});

describe("useMcp — 401 handling", () => {
  beforeEach(() => {
    signinRedirect.mockReset();
    mockAuth.user = {
      access_token: "test-token",
      profile: { tenant_id: "tenant-1", name: "Tester" }
    };
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
