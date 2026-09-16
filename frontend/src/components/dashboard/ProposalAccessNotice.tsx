import { MCPError } from "@/lib/mcp";

export function isProposalRoleRestricted(error: unknown): boolean {
  return error instanceof MCPError && error.code === "INSUFFICIENT_ROLE";
}

export function ProposalAccessNotice() {
  return <p className="py-6 text-sm text-muted-foreground" role="status">
    Proposal review is available to workspace staff. Your contact directory is still available.
  </p>;
}
