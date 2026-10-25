import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MCPError } from "@/lib/mcp";
import { PersonForm } from "@/routes/pages/People";

const mutateAsync = vi.hoisted(() => vi.fn());

vi.mock("@/hooks/useMcp", () => ({
  useMcpMutation: () => ({ mutateAsync, isPending: false }),
  useAllPeople: vi.fn(),
  usePerson: vi.fn(),
  usePhotos: vi.fn(),
  useTags: vi.fn()
}));

vi.mock("@/routes", () => ({ AuditList: () => null }));

describe("PersonForm", () => {
  beforeEach(() => {
    mutateAsync.mockReset();
    mutateAsync.mockRejectedValue(new MCPError("INSUFFICIENT_SCOPE", "missing required scope(s): person:write"));
  });

  it("shows an insufficient-scope error, keeps the values, and re-enables submit", async () => {
    render(<PersonForm onSaved={vi.fn()} />);

    const displayName = screen.getByLabelText("Display name");
    await userEvent.type(displayName, "Ada Lovelace");
    await userEvent.click(screen.getByRole("button", { name: "Create person" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your sign-in is missing the permission person:write. Sign out and back in; if it persists your workspace admin must grant it."
    );
    expect(displayName).toHaveValue("Ada Lovelace");
    expect(screen.getByRole("button", { name: "Create person" })).not.toBeDisabled();
  });
});
