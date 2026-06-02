/**
 * Inbox queries + mutations.
 *
 * Three parallel infinite queries (needs-review, snoozed, resolved) plus
 * approve/reject/snooze mutations with optimistic updates and sonner
 * toasts (with undo within the client window).
 *
 * Optimistic-removal is essential for the "single keystroke" feel:
 * approve fires, list shrinks immediately, toast appears with undo
 * button. On backend error, list reverts and toast surfaces the error.
 */
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { toast } from "sonner";
import { useQueryKeys } from "@/hooks/useMcp";
import { tools, UnauthorizedError, MCPError } from "@/lib/mcp";
import type {
  BulkActionResult,
  ListPendingProposalsResult,
  ProposalStatusFilter,
  RejectMode,
  Tier,
  UUID,
} from "@/lib/types";

export type InboxFilters = {
  status: ProposalStatusFilter;
  agentSlugs?: string[];
  confidenceMin?: number;
  confidenceMax?: number;
  conflictsOnly?: boolean;
  hipaaOnly?: boolean;
  crossTenantOnly?: boolean;
  caseFileOnly?: boolean;
  tenantIds?: UUID[];
};

const PAGE_LIMIT = 50;

function toBackendArgs(f: InboxFilters, cursor?: string): Record<string, unknown> {
  return {
    status: f.status,
    agent_slugs: f.agentSlugs,
    confidence_min: f.confidenceMin,
    confidence_max: f.confidenceMax,
    conflicts_only: f.conflictsOnly,
    hipaa_only: f.hipaaOnly,
    cross_tenant_only: f.crossTenantOnly,
    case_file_only: f.caseFileOnly,
    tenant_ids: f.tenantIds,
    cursor,
    limit: PAGE_LIMIT,
  };
}

export function useInboxList(filters: InboxFilters) {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const qk = useQueryKeys();
  return useInfiniteQuery<ListPendingProposalsResult>({
    queryKey: qk.inbox(filters),
    enabled: Boolean(token),
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      try {
        return await tools.listPendingProposals(
          toBackendArgs(filters, pageParam as string | undefined),
          token ?? "",
        );
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

/** Walk every page in the InfiniteData and splice out a proposal_id. */
function removeProposalFromPages(
  pages: InfiniteData<ListPendingProposalsResult>,
  proposalId: UUID,
): InfiniteData<ListPendingProposalsResult> {
  return {
    ...pages,
    pages: pages.pages.map((p) => {
      const proposals = p.proposals.filter((x) => x.proposal_id !== proposalId);
      const clusters = p.clusters
        .map((c) => ({
          ...c,
          proposal_ids: c.proposal_ids.filter((id) => id !== proposalId),
        }))
        .filter((c) => c.proposal_ids.length > 0);
      return { ...p, proposals, clusters };
    }),
  };
}

type ApproveArgs = {
  proposalId: UUID;
  tier: Tier;
  typedPhrase?: string | null;
  fieldChoices?: Record<string, "master" | "proposed" | "custom"> | null;
  timeToDecideSec?: number | null;
  keyboardPath?: boolean;
};

export function useApproveMutation(filters: InboxFilters) {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  const key = qk.inbox(filters);

  return useMutation({
    mutationFn: async ({ proposalId, tier, typedPhrase, fieldChoices, timeToDecideSec, keyboardPath }: ApproveArgs) => {
      return await tools.approveProposal(
        {
          proposal_id: proposalId,
          tier_assigned: tier,
          typed_phrase: typedPhrase ?? null,
          field_choices: fieldChoices ?? null,
          time_to_decide_sec: timeToDecideSec ?? null,
          keyboard_path: Boolean(keyboardPath),
          typed_phrase_used: Boolean(typedPhrase),
        },
        token,
      );
    },
    onMutate: async ({ proposalId }) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<InfiniteData<ListPendingProposalsResult>>(key);
      if (previous) {
        queryClient.setQueryData<InfiniteData<ListPendingProposalsResult>>(
          key,
          removeProposalFromPages(previous, proposalId),
        );
      }
      return { previous };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.previous) queryClient.setQueryData(key, ctx.previous);
      const msg = err instanceof MCPError ? err.message : (err as Error).message;
      toast.error(`Approve failed: ${msg}. Reverted.`);
    },
    onSuccess: (_data, vars) => {
      toast.success("Approved", {
        action: {
          label: "Undo (4s)",
          onClick: () => {
            tools
              .rejectProposal(
                { proposal_id: vars.proposalId, mode: "undo" },
                token,
              )
              .then(() => {
                toast.message("Undone");
                queryClient.invalidateQueries({ queryKey: qk.inboxRoot });
              })
              .catch((e: unknown) => {
                const m = e instanceof Error ? e.message : "unknown error";
                toast.error(`Undo failed: ${m}`);
              });
          },
        },
        duration: 4000,
      });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.inboxRoot });
    },
  });
}

type RejectArgs = {
  proposalId: UUID;
  mode?: RejectMode;
  reason?: string | null;
  tier?: Tier;
  keyboardPath?: boolean;
  suppressionAggregateId?: UUID | null;
  suppressionFieldName?: string | null;
};

export function useRejectMutation(filters: InboxFilters) {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  const key = qk.inbox(filters);

  return useMutation({
    mutationFn: async (args: RejectArgs) =>
      tools.rejectProposal(
        {
          proposal_id: args.proposalId,
          mode: args.mode ?? "reject",
          reason: args.reason ?? null,
          tier_assigned: args.tier ?? 1,
          keyboard_path: Boolean(args.keyboardPath),
          suppression_aggregate_id: args.suppressionAggregateId ?? null,
          suppression_field_name: args.suppressionFieldName ?? null,
        },
        token,
      ),
    onMutate: async ({ proposalId }) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<InfiniteData<ListPendingProposalsResult>>(key);
      if (previous) {
        queryClient.setQueryData(key, removeProposalFromPages(previous, proposalId));
      }
      return { previous };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.previous) queryClient.setQueryData(key, ctx.previous);
      const msg = err instanceof MCPError ? err.message : (err as Error).message;
      toast.error(`Reject failed: ${msg}. Reverted.`);
    },
    onSuccess: (_data, vars) => {
      toast.success("Rejected", {
        description: vars.mode === "mute" ? "Agent rule muted." : undefined,
      });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.inboxRoot });
    },
  });
}

type SnoozeArgs = {
  proposalId: UUID;
  snoozeUntil: Date;
  reason?: "wait_for_event" | "tomorrow" | "end_of_week" | "next_monday" | "custom";
  keyboardPath?: boolean;
};

export function useSnoozeMutation(filters: InboxFilters) {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  const key = qk.inbox(filters);

  return useMutation({
    mutationFn: async (args: SnoozeArgs) =>
      tools.snoozeProposal(
        {
          proposal_id: args.proposalId,
          snooze_until: args.snoozeUntil.toISOString(),
          snooze_reason: args.reason ?? "custom",
          keyboard_path: Boolean(args.keyboardPath),
        },
        token,
      ),
    onMutate: async ({ proposalId }) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<InfiniteData<ListPendingProposalsResult>>(key);
      if (previous) {
        queryClient.setQueryData(key, removeProposalFromPages(previous, proposalId));
      }
      return { previous };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.previous) queryClient.setQueryData(key, ctx.previous);
      const msg = err instanceof MCPError ? err.message : (err as Error).message;
      toast.error(`Snooze failed: ${msg}. Reverted.`);
    },
    onSuccess: () => {
      toast.success("Snoozed");
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.inboxRoot });
    },
  });
}

export function useBulkApproveMutation() {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: { proposalIds: UUID[]; typedPhrase?: string | null; tier?: Tier }) =>
      tools.bulkApprove(
        {
          proposal_ids: args.proposalIds,
          typed_phrase: args.typedPhrase ?? null,
          tier_assigned: args.tier ?? 1,
          keyboard_path: true,
        },
        token,
      ),
    onSuccess: (data: BulkActionResult) => {
      const applied = data.applied ?? 0;
      const skipped = data.skipped.length;
      const summary = skipped > 0 ? `${applied} applied, ${skipped} skipped` : `${applied} applied`;
      toast.success(`Bulk approve: ${summary}`, { duration: 8000 });
    },
    onError: (err) => {
      const msg = err instanceof MCPError ? err.message : (err as Error).message;
      toast.error(`Bulk approve failed: ${msg}`);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.inboxRoot });
    },
  });
}

export function useBulkRejectMutation() {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: { proposalIds: UUID[]; reason?: string | null; tier?: Tier }) =>
      tools.bulkReject(
        {
          proposal_ids: args.proposalIds,
          reason: args.reason ?? null,
          tier_assigned: args.tier ?? 1,
          keyboard_path: true,
        },
        token,
      ),
    onSuccess: (data: BulkActionResult) => {
      const rejected = data.rejected ?? 0;
      const skipped = data.skipped.length;
      toast.success(`Bulk reject: ${rejected} rejected, ${skipped} skipped`, { duration: 8000 });
    },
    onError: (err) => {
      const msg = err instanceof MCPError ? err.message : (err as Error).message;
      toast.error(`Bulk reject failed: ${msg}`);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.inboxRoot });
    },
  });
}

export function useResolveConflictKeepBothMutation() {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: {
      primaryProposalId: UUID;
      conflictingProposalId: UUID;
      sourceTags: Record<UUID, string>;
    }) =>
      tools.resolveConflictKeepBoth(
        {
          primary_proposal_id: args.primaryProposalId,
          conflicting_proposal_id: args.conflictingProposalId,
          source_tags: args.sourceTags,
          tier_assigned: 3,
        },
        token,
      ),
    onSuccess: () => {
      toast.success("Kept both as facts with source tags");
    },
    onError: (err) => {
      const msg = err instanceof MCPError ? err.message : (err as Error).message;
      toast.error(`Conflict resolve failed: ${msg}`);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.inboxRoot });
    },
  });
}

export function useRevertAutoAppliedMutation() {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: { proposalId: UUID; reason?: string | null }) =>
      tools.revertAutoApplied(
        { proposal_id: args.proposalId, reason: args.reason ?? null },
        token,
      ),
    onSuccess: (data) => {
      toast.success(`Reverted (${data.age_seconds}s after auto-apply)`);
    },
    onError: (err) => {
      const msg = err instanceof MCPError ? err.message : (err as Error).message;
      toast.error(`Revert failed: ${msg}`);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk.inboxRoot });
    },
  });
}
