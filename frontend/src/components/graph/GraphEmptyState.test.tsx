import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GraphEmptyState } from "./GraphEmptyState";

describe("graph projection empty state", () => {
  it("does not ask for a UUID when a person is already selected", () => {
    render(<GraphEmptyState rootPersonId="person-123" />);
    expect(screen.getByText(/No relationships are available for this person yet/)).toBeInTheDocument();
    expect(screen.queryByText(/Select a person UUID/)).not.toBeInTheDocument();
  });
  it("explains the workspace projection without inventing relationships", () => {
    render(<GraphEmptyState />);
    expect(screen.getByText(/No relationships are available in this workspace yet/)).toBeInTheDocument();
  });
});
