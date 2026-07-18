import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import type { InfiniteData } from "@tanstack/react-query";

import { makeTestQueryClient, withQueryClient, makeInboxPages } from "@/test/utils";
import { tenantKey } from "@/lib/queryKeys";
import { tools, MCPError, UnauthorizedError } from "@/lib/mcp";
import { isRenderableQueryError, queryViewState } from "@/lib/queryState";
import type { InboxFilters } from "@/hooks/useInbox";
import type { ListPendingProposalsResult } from "@/lib/types";

// --- Mocks: auth token + spyable login redirect, and a no-op toaster.
const signinRedirect = vi.fn();
vi.mock("react-oidc-context", () => ({
  useAuth: () => ({
    user: { access_token: "test-token", profile: { tenant_id: "tenant-1", name: "Tester" } },
    signinRedirect
  })
}));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    message: vi.fn()
  })
}));

const FILTERS: InboxFilters = { status: "proposed" };
const KEY = tenantKey("tenant-1").inbox(FILTERS);

function proposalIds(data: InfiniteData<ListPendingProposalsResult> | undefined): string[] {
  return (data?.pages ?? []).flatMap((p) => p.proposals.map((x) => x.proposal_id));
}

describe("inbox approve/reject optimistic rollback", () => {
  beforeEach(() => {
    signinRedirect.mockReset();
  });

  it("reverts the optimistic removal when approve fails on the backend", async () => {
    const { useApproveMutation } = await import("@/hooks/useInbox");

    const client = makeTestQueryClient();
    client.setQueryData(KEY, makeInboxPages(["p1", "p2"]));

    // Backend rejects the approve → onError must restore the removed row.
    vi.spyOn(tools, "approveProposal").mockRejectedValue(new MCPError(500, "boom"));

    const { result } = renderHook(() => useApproveMutation(FILTERS), {
      wrapper: withQueryClient(client)
    });

    result.current.mutate({ proposalId: "p1", tier: 1 });

    await waitFor(() => expect(result.current.isError).toBe(true));

    // Rolled back: BOTH proposals are present again (no silent data loss).
    const after = client.getQueryData<InfiniteData<ListPendingProposalsResult>>(KEY);
    expect(proposalIds(after).sort()).toEqual(["p1", "p2"]);
  });

  it("keeps the optimistic removal when approve succeeds", async () => {
    const { useApproveMutation } = await import("@/hooks/useInbox");

    const client = makeTestQueryClient();
    client.setQueryData(KEY, makeInboxPages(["p1", "p2"]));

    vi.spyOn(tools, "approveProposal").mockResolvedValue({
      applied: true,
      action_event_id: "evt-1",
      inbox_decision_id: "dec-1"
    });

    const { result } = renderHook(() => useApproveMutation(FILTERS), {
      wrapper: withQueryClient(client)
    });

    result.current.mutate({ proposalId: "p1", tier: 1 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const after = client.getQueryData<InfiniteData<ListPendingProposalsResult>>(KEY);
    expect(proposalIds(after)).toEqual(["p2"]);
  });

  it("reverts the optimistic removal when reject fails on the backend", async () => {
    const { useRejectMutation } = await import("@/hooks/useInbox");

    const client = makeTestQueryClient();
    client.setQueryData(KEY, makeInboxPages(["p1", "p2"]));

    vi.spyOn(tools, "rejectProposal").mockRejectedValue(new MCPError(503, "unavailable"));

    const { result } = renderHook(() => useRejectMutation(FILTERS), {
      wrapper: withQueryClient(client)
    });

    result.current.mutate({ proposalId: "p2", mode: "reject" });

    await waitFor(() => expect(result.current.isError).toBe(true));

    const after = client.getQueryData<InfiniteData<ListPendingProposalsResult>>(KEY);
    expect(proposalIds(after).sort()).toEqual(["p1", "p2"]);
  });
});

describe("inbox query error surfacing (simulated 500 vs 401)", () => {
  it("surfaces a simulated 500 as a RENDERABLE error (error state, not empty)", async () => {
    const { useInboxList } = await import("@/hooks/useInbox");

    vi.spyOn(tools, "listPendingProposals").mockRejectedValue(
      new MCPError(500, "MCP request failed with HTTP 500")
    );

    const client = makeTestQueryClient();
    const { result } = renderHook(() => useInboxList(FILTERS), {
      wrapper: withQueryClient(client)
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    // The UI branches on these exact helpers → error state, NOT "inbox zero".
    expect(isRenderableQueryError(result.current)).toBe(true);
    expect(queryViewState(result.current)).toBe("error");
  });

  it("does NOT render an error state on a 401 (login redirect instead)", async () => {
    const { useInboxList } = await import("@/hooks/useInbox");

    vi.spyOn(tools, "listPendingProposals").mockRejectedValue(new UnauthorizedError());

    const client = makeTestQueryClient();
    const { result } = renderHook(() => useInboxList(FILTERS), {
      wrapper: withQueryClient(client)
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    expect(isRenderableQueryError(result.current)).toBe(false);
    expect(queryViewState(result.current)).toBe("loading");
    expect(signinRedirect).toHaveBeenCalled();
  });
});
