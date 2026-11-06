import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { PAT_SCOPE_CHIPS } from "@/components/profile/PersonalAccessTokensSection";
import { PAT_SCOPE_CATALOGUE } from "@/lib/types";

function backendScopeCatalogue(): string[] {
  const source = readFileSync(
    resolve(process.cwd(), "../backend/contact_ops/pat_scopes.py"),
    "utf8"
  );
  const body = source.match(/PAT_SCOPE_CATALOGUE:[\s\S]*?=\s*\(([\s\S]*?)\n\)/)?.[1];
  if (!body) throw new Error("backend PAT scope catalogue was not found");
  return [...body.matchAll(/"([^"]+)"/g)].map((match) => match[1]);
}

describe("personal access token scope contract", () => {
  it("renders every backend-catalogued scope exactly once", () => {
    expect(PAT_SCOPE_CHIPS).toEqual(backendScopeCatalogue());
    expect(PAT_SCOPE_CHIPS).toEqual([...PAT_SCOPE_CATALOGUE]);
    expect(new Set(PAT_SCOPE_CHIPS).size).toBe(PAT_SCOPE_CATALOGUE.length);
    expect(PAT_SCOPE_CHIPS).toContain("contacts:read_personal");
  });
});
