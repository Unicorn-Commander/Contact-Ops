import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MCPError } from "@/lib/mcp";
import { TenantSwitcher } from "@/components/domain/TenantSwitcher";

const tenantQuery = vi.hoisted(() => ({
  data: undefined as { items: Array<Record<string, unknown>>; count: number } | undefined,
  error: null as unknown,
  isError: false,
  isLoading: false
}));
const switchMutation = vi.hoisted(() => ({ isPending: false, mutate: vi.fn() }));
const profile = vi.hoisted(() => ({ tenant_id: "t1", tenant_slug: "alpha-team" }));

vi.mock("react-oidc-context", () => ({
  useAuth: () => ({ user: { profile } })
}));

vi.mock("@/hooks/useMcp", () => ({
  useActiveTenantId: () => "t1",
  useSwitchWorkspace: () => switchMutation,
  useTenants: () => tenantQuery
}));

describe("TenantSwitcher", () => {
  beforeEach(() => {
    tenantQuery.data = {
      items: [
        { tenant_id: "t1", slug: "alpha", display_name: "Alpha Workspace", role: "admin" },
        { tenant_id: "t2", slug: "beta", display_name: "Beta Workspace", role: "member" }
      ],
      count: 2
    };
    tenantQuery.error = null;
    tenantQuery.isError = false;
    tenantQuery.isLoading = false;
    switchMutation.isPending = false;
    switchMutation.mutate.mockReset();
    profile.tenant_id = "t1";
    profile.tenant_slug = "alpha-team";
  });

  it("lists every workspace, marks the current one, and starts the existing switch flow", async () => {
    render(<TenantSwitcher />);

    await userEvent.click(screen.getByRole("button", { name: "Switch workspace" }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("Alpha Workspace")).toBeInTheDocument();
    expect(within(menu).getByText("Beta Workspace")).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: /Alpha Workspace/ })).toHaveAttribute("aria-checked", "true");

    await userEvent.click(screen.getByRole("menuitemradio", { name: /Beta Workspace/ }));
    expect(switchMutation.mutate).toHaveBeenCalledWith(
      "t2",
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) })
    );
  });

  it("keeps the current workspace named and exposes list errors in the open dropdown", async () => {
    tenantQuery.data = undefined;
    tenantQuery.isError = true;
    tenantQuery.error = new MCPError("INSUFFICIENT_SCOPE", "missing required scope(s): tenant:read");

    render(<TenantSwitcher />);

    expect(screen.getByText("Alpha Team")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Switch workspace" }));

    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load workspaces");
    expect(screen.getByRole("alert")).toHaveTextContent("tenant:read");
  });
});
