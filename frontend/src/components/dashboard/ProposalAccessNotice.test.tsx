import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MCPError, UnauthorizedError } from "@/lib/mcp";
import { ProposalAccessNotice, isProposalRoleRestricted } from "./ProposalAccessNotice";

describe("proposal permission explanation", () => {
  it("explains the role boundary without suggesting retries or showing proposals", () => {
    render(<ProposalAccessNotice />);
    expect(screen.getByRole("status")).toHaveTextContent("available to workspace staff");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
  it("does not hide server failures or expired authentication as role restrictions", () => {
    expect(isProposalRoleRestricted(new MCPError("INSUFFICIENT_ROLE", "requires STAFF"))).toBe(true);
    expect(isProposalRoleRestricted(new MCPError(500, "failure"))).toBe(false);
    expect(isProposalRoleRestricted(new UnauthorizedError())).toBe(false);
    expect(isProposalRoleRestricted(null)).toBe(false);
  });
});
