import { describe, expect, it } from "vitest";
import { PAT_SCOPE_CHIPS } from "@/components/profile/PersonalAccessTokensSection";
import { PAT_SCOPE_CATALOGUE } from "@/lib/types";

describe("personal access token scope contract", () => {
  it("renders every backend-catalogued scope exactly once", () => {
    expect(PAT_SCOPE_CHIPS).toEqual([...PAT_SCOPE_CATALOGUE]);
    expect(new Set(PAT_SCOPE_CHIPS).size).toBe(PAT_SCOPE_CATALOGUE.length);
    expect(PAT_SCOPE_CHIPS).toContain("contacts:read_personal");
  });
});
