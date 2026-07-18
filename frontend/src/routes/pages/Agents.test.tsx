import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { makeTestQueryClient, withQueryClient } from "@/test/utils";
import { tools } from "@/lib/mcp";
import type { AgentGovernance } from "@/lib/types";

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
    info: vi.fn(),
    message: vi.fn()
  })
}));

// The Agents module pulls AuditList from the router barrel only for its activity
// feed; the kill-switch banner never touches it. Stub the barrel so importing the
// banner does not drag the whole route tree into the test.
vi.mock("@/routes", () => ({
  AuditList: () => null
}));

/** A minimally-valid governance envelope for the banner (active fleet by default). */
function governance(over: Partial<AgentGovernance> = {}): AgentGovernance {
  return {
    agents_paused: false,
    agents_paused_reason: null,
    agents_paused_at: null,
    agents_paused_by: null,
    ...over
  };
}

describe("/agents Command Center kill-switch toggle", () => {
  beforeEach(() => {
    signinRedirect.mockReset();
  });

  it("fires set_agents_paused({ paused: true }) when an admin pauses the fleet", async () => {
    // Import lazily so the react-oidc-context / sonner mocks are applied first.
    const { KillSwitchBanner } = await import("@/routes/pages/Agents");

    // Admin, fleet currently ACTIVE → the banner renders the "Pause" toggle.
    vi.spyOn(tools, "getAgentGovernance").mockResolvedValue(governance());
    const setPaused = vi
      .spyOn(tools, "setAgentsPaused")
      .mockResolvedValue(
        governance({ agents_paused: true, agents_paused_reason: "Paused by an administrator" })
      );

    const client = makeTestQueryClient();
    render(<KillSwitchBanner />, { wrapper: withQueryClient(client) });

    // Toggle the kill-switch: click "Pause all agents", then confirm in the reason
    // dialog (left blank → the default reason is submitted).
    const toggle = await screen.findByRole("button", { name: /pause all agents/i });
    await userEvent.click(toggle);

    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /pause all agents/i }));

    // The money path: the pause mutation fires with the expected args + token.
    await waitFor(() =>
      expect(setPaused).toHaveBeenCalledWith(
        { paused: true, reason: "Paused by an administrator" },
        "test-token"
      )
    );
  });

  it("fires set_agents_paused({ paused: false }) when an admin resumes a paused fleet", async () => {
    const { KillSwitchBanner } = await import("@/routes/pages/Agents");

    // Fleet currently PAUSED → the banner renders the "Resume" toggle.
    vi.spyOn(tools, "getAgentGovernance").mockResolvedValue(
      governance({
        agents_paused: true,
        agents_paused_reason: "maintenance",
        agents_paused_by: "admin@acme"
      })
    );
    const setPaused = vi
      .spyOn(tools, "setAgentsPaused")
      .mockResolvedValue(governance({ agents_paused: false }));

    const client = makeTestQueryClient();
    render(<KillSwitchBanner />, { wrapper: withQueryClient(client) });

    const toggle = await screen.findByRole("button", { name: /resume agents/i });
    await userEvent.click(toggle);

    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /resume agents/i }));

    await waitFor(() =>
      expect(setPaused).toHaveBeenCalledWith(
        { paused: false, reason: "Resumed by an administrator" },
        "test-token"
      )
    );
  });
});
