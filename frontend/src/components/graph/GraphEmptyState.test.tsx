import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GraphEmptyState } from "./GraphEmptyState";

describe("graph projection empty state", () => {
  it("does not ask for a UUID when a person is already selected", () => {
    render(<GraphEmptyState rootPersonId="person-123" />);
    expect(screen.getByText(/This person's graph projection is empty/)).toBeInTheDocument();
    expect(screen.queryByText(/Select a person UUID/)).not.toBeInTheDocument();
  });
  it("explains the workspace projection without inventing relationships", () => {
    render(<GraphEmptyState />);
    expect(screen.getByText(/This workspace's graph projection is empty/)).toBeInTheDocument();
  });
});
