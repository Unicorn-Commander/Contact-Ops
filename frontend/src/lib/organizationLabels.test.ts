import { describe, expect, it } from "vitest";
import { withOrganizationLabels } from "./mcp";

describe("organization directory labels", () => {
  it("maps the API label while preserving pagination and missing affiliations", () => {
    const result = withOrganizationLabels({items: [
      {person_id:"a", display_name:"A", current_org_name:"Visible employer", current_org:"stale"},
      {person_id:"b", display_name:"B", current_org_id:"foreign", current_org_name:null, current_org:"stale forbidden label"},
      {person_id:"c", display_name:"C", current_org:"Legacy employer"}
    ], count:3, next_cursor:"next"});
    expect(result.items.map(p => p.current_org)).toEqual(["Visible employer",null,"Legacy employer"]);
    expect(result.next_cursor).toBe("next");
  });
});
