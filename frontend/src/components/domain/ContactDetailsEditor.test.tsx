import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ContactDetailsEditor } from "@/components/domain/ContactDetailsEditor";

// Capture every mutation call across the component's sections. The section
// components select the mutation *kind* by the tool wrapper passed to
// `useMcpMutation`, but each call returns the SAME recorder here, so the test
// asserts on the args (which carry the tool-specific id key) rather than the
// wrapper identity.
const mutateAsync = vi.fn(async () => ({}));
const pending = { isPending: false };

vi.mock("@/hooks/useMcp", () => ({
  useMcpMutation: () => ({ mutateAsync, isPending: pending.isPending })
}));

beforeEach(() => {
  mutateAsync.mockClear();
  pending.isPending = false;
});

describe("ContactDetailsEditor", () => {
  it("makes an IM handle primary via update_im_handle", async () => {
    render(
      <ContactDetailsEditor
        personId="p1"
        data={{
          im_handles: [{ id: "h1", protocol: "x", handle: "@acme", label: "work" }]
        }}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: /make primary handle/i }));

    expect(mutateAsync).toHaveBeenCalledTimes(1);
    expect(mutateAsync).toHaveBeenCalledWith({ im_handle_id: "h1", is_primary: true });
  });

  it("edits an IM handle in place and only sends changed fields", async () => {
    render(
      <ContactDetailsEditor
        personId="p1"
        data={{ im_handles: [{ id: "h1", protocol: "x", handle: "@acme", label: "work" }] }}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: /edit handle/i }));
    const handleInput = screen.getByLabelText("Edit handle");
    await userEvent.clear(handleInput);
    await userEvent.type(handleInput, "@acmehq");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(mutateAsync).toHaveBeenCalledTimes(1);
    expect(mutateAsync).toHaveBeenCalledWith({ im_handle_id: "h1", handle: "@acmehq" });
  });

  it("hides the primary affordance for an already-primary URL and shows it for others", () => {
    render(
      <ContactDetailsEditor
        personId="p1"
        data={{
          urls: [
            { id: "u1", url: "https://a.example", type: "profile", is_primary: true },
            { id: "u2", url: "https://b.example", type: "blog" }
          ]
        }}
      />
    );

    // Exactly one row is not primary -> exactly one "make primary" button.
    expect(screen.getAllByRole("button", { name: /make primary url/i })).toHaveLength(1);
  });

  it("edits a URL in place and sends only the changed url", async () => {
    render(
      <ContactDetailsEditor
        personId="p1"
        data={{ urls: [{ id: "u1", url: "https://a.example", type: "profile" }] }}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: /edit url/i }));
    const urlInput = screen.getByLabelText("Edit URL");
    await userEvent.clear(urlInput);
    await userEvent.type(urlInput, "https://new.example");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(mutateAsync).toHaveBeenCalledWith({ url_id: "u1", url: "https://new.example" });
  });

  it("edits an identifier in place via update_identifier", async () => {
    render(
      <ContactDetailsEditor
        personId="p1"
        data={{ identifiers: [{ id: "i1", namespace: "github", value: "acme" }] }}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: /edit identifier/i }));
    const valueInput = screen.getByLabelText("Edit value");
    await userEvent.clear(valueInput);
    await userEvent.type(valueInput, "acme-inc");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(mutateAsync).toHaveBeenCalledWith({ identifier_id: "i1", value: "acme-inc" });
  });

  it("cancels an in-place edit without mutating", async () => {
    render(
      <ContactDetailsEditor
        personId="p1"
        data={{ im_handles: [{ id: "h1", protocol: "x", handle: "@acme" }] }}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: /edit handle/i }));
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /edit handle/i })).toBeInTheDocument();
  });
});
