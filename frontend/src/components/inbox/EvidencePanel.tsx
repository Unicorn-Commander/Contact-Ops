/**
 * EvidencePanel - source events + reasoning + Laminar deep-link.
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
import { useQueryKeys } from "@/hooks/useMcp";
import { tools, UnauthorizedError } from "@/lib/mcp";
import type { ProposalEvidence, UUID } from "@/lib/types";
import { MonoNumeric, Spinner } from "@/design-system";

export type EvidencePanelProps = {
  proposalId: UUID;
  /** Caller may force-expand (e.g. when entering a Tier-2 proposal). */
  defaultExpanded?: boolean;
};

export function EvidencePanel({ proposalId, defaultExpanded = false }: EvidencePanelProps) {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const qk = useQueryKeys();
  const [expanded, setExpanded] = useState(defaultExpanded);

  const query = useQuery<ProposalEvidence>({
    queryKey: qk.proposalEvidence(proposalId),
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
      className="rounded-md border border-border bg-card shadow-[var(--shadow-1)]"
    >
      <header className="flex items-center justify-between gap-co-8 px-co-12 py-co-8">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="focus-ring flex items-center gap-co-6 rounded-[var(--radius-sm)] text-13 font-medium hover:text-link"
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
            className="inline-flex items-center gap-co-4 text-12 text-link hover:underline"
            title="Open in Laminar"
          >
            <span>Trace</span>
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </header>

      {expanded && (
        <div className="space-y-co-12 border-t border-border p-co-12">
          {query.isPending && (
            <div className="space-y-co-8">
              <div className="flex items-center gap-co-6 text-12 text-muted-foreground">
                <Spinner size="sm" label="" />
                <span>Loading evidence</span>
              </div>
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-4 w-5/6" />
            </div>
          )}
          {query.isError && (
            <p className="text-12 text-destructive">
              Failed to load evidence: {(query.error as Error).message}
            </p>
          )}
          {query.data && (
            <>
              {query.data.reasoning && (
                <div className="space-y-co-4">
                  <p className="font-mono text-11 font-medium uppercase text-muted-foreground">
                    Agent reasoning
                  </p>
                  <p className="whitespace-pre-wrap rounded-md border border-border bg-muted/35 p-co-8 text-13 text-foreground">
                    {query.data.reasoning}
                  </p>
                </div>
              )}
              {query.data.source_events.length > 0 && (
                <div className="space-y-co-4">
                  <p className="font-mono text-11 font-medium uppercase text-muted-foreground">
                    Source events ({query.data.source_events.length})
                  </p>
                  <ul className="space-y-co-4">
                    {query.data.source_events.map((e) => (
                      <li key={e.event_id} className="flex items-center justify-between gap-co-8 text-12">
                        <a
                          href={e.deep_link}
                          className="truncate hover:text-link hover:underline"
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
              <div className="grid grid-cols-3 gap-co-8 border-t border-border pt-co-8 text-12">
                <Stat label="Cost" value={`$${(query.data.cost_cents / 100).toFixed(4)}`} />
                <Stat label="In" value={`${query.data.tokens_input.toLocaleString()} tok`} />
                <Stat label="Out" value={`${query.data.tokens_output.toLocaleString()} tok`} />
              </div>
            </>
          )}
        </div>
      )}

      {!expanded && (
        <div className="px-co-12 pb-co-8">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-12 text-muted-foreground"
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
      <p className="font-mono text-11 uppercase text-muted-foreground">{label}</p>
      <MonoNumeric>{value}</MonoNumeric>
    </div>
  );
}
