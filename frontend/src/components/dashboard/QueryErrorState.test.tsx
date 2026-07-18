import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { QueryErrorState, errorDetail } from "@/components/dashboard/QueryErrorState";
import { MCPError, UnauthorizedError } from "@/lib/mcp";

describe("QueryErrorState", () => {
  it("renders an alert with an HTTP-500 detail and a working Retry (not an empty state)", async () => {
    const onRetry = vi.fn();
    render(
      <QueryErrorState
        title="Couldn't load the review queue"
        error={new MCPError(500, "MCP request failed with HTTP 500")}
        onRetry={onRetry}
      />
    );

    // Distinctly an ERROR surface, not "inbox zero".
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Couldn't load the review queue")).toBeInTheDocument();
    expect(screen.getByText(/HTTP 500/)).toBeInTheDocument();

    const retry = screen.getByRole("button", { name: /retry/i });
    await userEvent.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("disables Retry and shows a retrying label while a refetch is in flight", () => {
    render(<QueryErrorState onRetry={() => {}} retrying />);
    const retry = screen.getByRole("button", { name: /retrying/i });
    expect(retry).toBeDisabled();
  });

  it("derives readable detail lines from different error shapes", () => {
    expect(errorDetail(new MCPError(503, "x"))).toMatch(/HTTP 503/);
    expect(errorDetail(new MCPError("TOOL_ERROR", "tool blew up"))).toBe("tool blew up");
    expect(errorDetail(new UnauthorizedError())).toBe("Authentication expired");
    expect(errorDetail(undefined)).toMatch(/try again/i);
  });
});
