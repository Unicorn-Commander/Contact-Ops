import { describe, expect, it } from "vitest";
import { globalKey, tenantKey, userKey } from "@/lib/queryKeys";

/**
 * Money path: EVERY workspace-scoped query key must be prefixed with
 * ["t", <tenantId>] so a workspace switch's queryClient.clear() cannot leak
 * cross-tenant rows, and the prefix flips the instant the token swaps.
 */
describe("tenant query-key prefixing", () => {
  const qk = tenantKey("tenant-abc");

  it("prefixes every static key with the tenant sentinel + id", () => {
    for (const key of [qk.tools, qk.dashboard, qk.recentEvents, qk.tags, qk.inboxRoot]) {
      expect(key[0]).toBe("t");
      expect(key[1]).toBe("tenant-abc");
    }
  });

  it("prefixes every parametric key with the tenant sentinel + id", () => {
    expect(qk.person("p1").slice(0, 2)).toEqual(["t", "tenant-abc"]);
    expect(qk.personNames("p1").slice(0, 2)).toEqual(["t", "tenant-abc"]);
    expect(qk.org("o1").slice(0, 2)).toEqual(["t", "tenant-abc"]);
    expect(qk.people({ query: "x" }).slice(0, 2)).toEqual(["t", "tenant-abc"]);
    expect(qk.inbox({ status: "proposed" }).slice(0, 2)).toEqual(["t", "tenant-abc"]);
    expect(qk.photos("p1").slice(0, 2)).toEqual(["t", "tenant-abc"]);
  });

  it("re-prefixes when the active tenant changes (no shared identity)", () => {
    const a = tenantKey("tenant-a").inbox({ status: "proposed" });
    const b = tenantKey("tenant-b").inbox({ status: "proposed" });
    expect(a).not.toEqual(b);
    expect(a[1]).toBe("tenant-a");
    expect(b[1]).toBe("tenant-b");
  });

  it("keeps user-scoped keys tenant-INDEPENDENT under the 'g' sentinel", () => {
    // The workspace list + PATs must survive a queryClient.clear() switch.
    expect(userKey.tenants[0]).toBe("g");
    expect(userKey.personalAccessTokens[0]).toBe("g");
    expect(globalKey("anything")[0]).toBe("g");
  });
});
