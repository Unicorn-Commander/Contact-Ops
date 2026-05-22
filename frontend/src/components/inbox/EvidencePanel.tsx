/**
 * EvidencePanel — source events + reasoning + Laminar deep-link.
 *
 * Lazy-loads via useQuery only when expanded (avoids paying for the
 * evidence fetch on every proposal scroll). Source events deep-link
 * back into /people/:id or /orgs/:id.
 *
 * Used by the detail pane; the IntersectionObserver in TieredApproveButton
 * checks `data-evidence-panel` to know when the panel scrolled into view
 * for Tier 2 gating.
 */
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { useState } from "react";
import { useAuth } from "react-oidc-context";
import { formatDistanceToNowStrict } from "date-fns";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { tools, UnauthorizedError } from "@/lib/mcp";
import type { ProposalEvidence, UUID } from "@/lib/types";

export type EvidencePanelProps = {
  proposalId: UUID;
  /** Caller may force-expand (e.g. when entering a Tier-2 proposal). */
  defaultExpanded?: boolean;
};

export function EvidencePanel({ proposalId, defaultExpanded = false }: EvidencePanelProps) {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const [expanded, setExpanded] = useState(defaultExpanded);

  const query = useQuery<ProposalEvidence>({
    queryKey: ["proposal-evidence", proposalId],
    enabled: Boolean(token) && expanded,
    queryFn: async () => {
      try {
        return await tools.getProposalEvidence(proposalId, token);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
  });

  return (
    <section
      data-evidence-panel
      data-proposal-id={proposalId}
      className="rounded-md border bg-card"
    >
      <header className="flex items-center justify-between gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-2 text-sm font-medium hover:text-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
          aria-expanded={expanded}
        >
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          Evidence
        </button>
        {query.data?.laminar_trace_url && (
          <a
            href={query.data.laminar_trace_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-violet-500 hover:underline"
            title="Open in Laminar"
          >
            <span>Trace</span>
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </header>

      {expanded && (
        <div className="space-y-3 border-t p-3">
          {query.isPending && (
            <div className="space-y-2">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-4 w-5/6" />
            </div>
          )}
          {query.isError && (
            <p className="text-xs text-rose-500">
              Failed to load evidence: {(query.error as Error).message}
            </p>
          )}
          {query.data && (
            <>
              {query.data.reasoning && (
                <div className="space-y-1">
                  <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                    Agent reasoning
                  </p>
                  <p className="whitespace-pre-wrap text-sm text-foreground/90">
                    {query.data.reasoning}
                  </p>
                </div>
              )}
              {query.data.source_events.length > 0 && (
                <div className="space-y-1">
                  <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                    Source events ({query.data.source_events.length})
                  </p>
                  <ul className="space-y-1">
                    {query.data.source_events.map((e) => (
                      <li key={e.event_id} className="flex items-center justify-between gap-2 text-xs">
                        <a
                          href={e.deep_link}
                          className="truncate hover:text-violet-500 hover:underline"
                          title={e.title}
                        >
                          {e.title}
                        </a>
                        <span className="text-muted-foreground">
                          {formatDistanceToNowStrict(new Date(e.occurred_at), { addSuffix: true })}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="grid grid-cols-3 gap-2 border-t pt-2 text-xs">
                <Stat label="Cost" value={`$${(query.data.cost_cents / 100).toFixed(4)}`} />
                <Stat label="In" value={`${query.data.tokens_input.toLocaleString()} tok`} />
                <Stat label="Out" value={`${query.data.tokens_output.toLocaleString()} tok`} />
              </div>
            </>
          )}
        </div>
      )}

      {!expanded && (
        <div className="px-3 pb-2">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs text-muted-foreground"
            onClick={() => setExpanded(true)}
          >
            Reveal evidence
          </Button>
        </div>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="font-mono tabular-nums">{value}</p>
    </div>
  );
}
