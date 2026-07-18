/**
 * Shared test helpers: a React Query provider wrapper with retries disabled
 * (so a simulated failure surfaces immediately instead of after 3 retries) and
 * small fixture builders for the inbox money-path tests.
 */
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider, type InfiniteData } from "@tanstack/react-query";
import type { ListPendingProposalsResult, Proposal } from "@/lib/types";

/** A QueryClient tuned for tests: no retries, no refetch noise. */
export function makeTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, staleTime: Infinity },
      mutations: { retry: false }
    }
  });
}

/** Wrapper factory for renderHook / render that shares one QueryClient. */
export function withQueryClient(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

/** Build a minimally-valid Proposal for list fixtures (only fields the code touches matter). */
export function makeProposal(id: string, over: Partial<Proposal> = {}): Proposal {
  return {
    proposal_id: id,
    action_event_id: `evt-${id}`,
    agent_id: "agent.enricher",
    agent_version: "1.0.0",
    tenant_id: "tenant-1",
    tenant_slug: "acme",
    entity_id: `person-${id}`,
    entity_kind: "person",
    entity_display_name: `Person ${id}`,
    action_type: "person.update",
    payload_before: null,
    payload_after: {},
    confidence: 0.9,
    reversibility_class: "reversible",
    compliance: { hipaa: false, exposure: "none" },
    trust_tier_at_creation: 1,
    rationale: "test",
    created_at: new Date("2026-01-01T00:00:00Z").toISOString(),
    cross_tenant: false,
    touches_case_file: false,
    case_file_links: [],
    fields_changed: 1,
    is_dedup: false,
    is_edge: false,
    is_delete: false,
    bulk_count: 1,
    cluster_id: `cluster-${id}`,
    ...over
  } as Proposal;
}

/** Build the InfiniteData shape the inbox query caches (one page of proposals). */
export function makeInboxPages(
  proposalIds: string[]
): InfiniteData<ListPendingProposalsResult> {
  const proposals = proposalIds.map((id) => makeProposal(id));
  const page: ListPendingProposalsResult = {
    proposals,
    clusters: proposalIds.map((id) => ({
      cluster_id: `cluster-${id}`,
      cluster_kind: "entity",
      entity_id: `person-${id}`,
      entity_display_name: `Person ${id}`,
      tenant_id: "tenant-1",
      proposal_ids: [id],
      cumulative_confidence_avg: 0.9,
      agent_slugs: ["agent.enricher"],
      earliest_created_at: new Date("2026-01-01T00:00:00Z").toISOString(),
      latest_created_at: new Date("2026-01-01T00:00:00Z").toISOString()
    })),
    next_cursor: null,
    total_estimate: proposalIds.length
  };
  return { pages: [page], pageParams: [undefined] };
}
