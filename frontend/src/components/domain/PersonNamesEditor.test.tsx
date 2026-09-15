import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { makeTestQueryClient, withQueryClient } from "@/test/utils";
import { tools } from "@/lib/mcp";
import type { PersonName } from "@/lib/types";

vi.mock("react-oidc-context", () => ({
  useAuth: () => ({
    user: { access_token: "test-token", profile: { tenant_id: "tenant-1", name: "Tester" } },
    signinRedirect: vi.fn()
  })
}));

function name(over: Partial<PersonName> = {}): PersonName {
  return {
    person_name_id: "n1",
    name_type: "aka",
    value: "Dizzy",
    is_primary: false,
    ...over
  };
}

describe("PersonNamesEditor", () => {
  it("renders names and adds a new name through add_person_name", async () => {
    vi.spyOn(tools, "listPersonNames").mockResolvedValue({ items: [name()], count: 1 });
    const add = vi.spyOn(tools, "addPersonName").mockResolvedValue({});

    const { PersonNamesEditor } = await import("@/components/domain/PersonNamesEditor");
    const client = makeTestQueryClient();
    render(<PersonNamesEditor personId="p1" />, { wrapper: withQueryClient(client) });

    expect(await screen.findByText("Dizzy")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Name type"), "nickname");
    await userEvent.type(screen.getByLabelText("Name"), "Nicky");
    await userEvent.click(screen.getByRole("button", { name: /add/i }));

    await waitFor(() =>
      expect(add).toHaveBeenCalledWith(
        { person_id: "p1", name_type: "nickname", value: "Nicky" },
        "test-token"
      )
    );
  });

  it("fires set_primary_person_name and remove_person_name for a name", async () => {
    vi.spyOn(tools, "listPersonNames").mockResolvedValue({ items: [name()], count: 1 });
    const setPrimary = vi.spyOn(tools, "setPrimaryPersonName").mockResolvedValue({});
    const remove = vi.spyOn(tools, "removePersonName").mockResolvedValue({});

    const { PersonNamesEditor } = await import("@/components/domain/PersonNamesEditor");
    const client = makeTestQueryClient();
    render(<PersonNamesEditor personId="p1" />, { wrapper: withQueryClient(client) });

    await screen.findByText("Dizzy");

    await userEvent.click(screen.getByRole("button", { name: /make dizzy primary/i }));
    await waitFor(() =>
      expect(setPrimary).toHaveBeenCalledWith({ person_name_id: "n1" }, "test-token")
    );

    await userEvent.click(screen.getByRole("button", { name: /remove dizzy/i }));
    await waitFor(() =>
      expect(remove).toHaveBeenCalledWith({ person_name_id: "n1" }, "test-token")
    );
  });

  it("edits a name inline through update_person_name", async () => {
    vi.spyOn(tools, "listPersonNames").mockResolvedValue({ items: [name()], count: 1 });
    const update = vi.spyOn(tools, "updatePersonName").mockResolvedValue({});

    const { PersonNamesEditor } = await import("@/components/domain/PersonNamesEditor");
    const client = makeTestQueryClient();
    render(<PersonNamesEditor personId="p1" />, { wrapper: withQueryClient(client) });

    await screen.findByText("Dizzy");
    await userEvent.click(screen.getByRole("button", { name: /edit dizzy/i }));

    const editInput = screen.getByLabelText("Edit Dizzy");
    await userEvent.clear(editInput);
    await userEvent.type(editInput, "Dizzy G");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        { person_name_id: "n1", value: "Dizzy G" },
        "test-token"
      )
    );
  });
});
