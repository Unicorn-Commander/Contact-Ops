import { describe, expect, it, vi } from "vitest";
import { withOrganizationLabels, callTool } from "./mcp";

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

it("retains a structured tool error code when human content is plain text", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true, status: 200, json: async () => ({jsonrpc:"2.0",id:"test",result:{
      isError:true, content:[{type:"text",text:"requires STAFF"}],
      structuredContent:{code:"INSUFFICIENT_ROLE",message:"requires STAFF"}
    }})
  }));
  try {
    await expect(callTool("list_pending_proposals",{},"synthetic-token"))
      .rejects.toMatchObject({code:"INSUFFICIENT_ROLE",message:"requires STAFF"});
  } finally { vi.unstubAllGlobals(); }
});
