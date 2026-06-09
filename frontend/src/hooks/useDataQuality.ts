/**
 * Data-quality queries + safe-fix mutations.
 *
 * The report is read-only. The two fixes (clean org names / canonicalize tags)
 * run dry_run by default; callers pass `{ dryRun: false }` to apply. Applying
 * invalidates the report plus the org/tag caches so every surface refreshes.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { toast } from "sonner";
import { useQueryKeys } from "@/hooks/useMcp";
import { tools, UnauthorizedError, MCPError } from "@/lib/mcp";
import type { CanonicalizeTagsResult, CleanOrgNamesResult } from "@/lib/types";

export function useDataQualityReport() {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const qk = useQueryKeys();
  return useQuery({
    queryKey: qk.dataQuality,
    enabled: Boolean(token),
    queryFn: async () => {
      try {
        return await tools.dataQualityReport(token ?? "", { sample_limit: 500 });
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
  });
}

function errMessage(err: unknown): string {
  return err instanceof MCPError ? err.message : (err as Error).message;
}

export function useCleanOrgNamesMutation() {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: (args: { dryRun: boolean }) =>
      tools.cleanOrgNames({ dry_run: args.dryRun }, token),
    onSuccess: (data: CleanOrgNamesResult) => {
      if (!data.dry_run) {
        toast.success(`Cleaned ${data.changed_count} organization names`, { duration: 6000 });
        void queryClient.invalidateQueries({ queryKey: qk.dataQuality });
        // Org rows changed everywhere — refetch the whole tenant scope (["t", id]).
        void queryClient.invalidateQueries({ queryKey: qk.dataQuality.slice(0, 2) });
      }
    },
    onError: (err) => toast.error(`Clean failed: ${errMessage(err)}`),
  });
}

export function useCanonicalizeTagsMutation() {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: (args: { dryRun: boolean }) =>
      tools.canonicalizeTags({ dry_run: args.dryRun }, token),
    onSuccess: (data: CanonicalizeTagsResult) => {
      if (!data.dry_run) {
        toast.success(`Canonicalized tags across ${data.memberships_changed} people`, {
          duration: 6000,
        });
        void queryClient.invalidateQueries({ queryKey: qk.dataQuality });
        void queryClient.invalidateQueries({ queryKey: qk.tags });
      }
    },
    onError: (err) => toast.error(`Tag cleanup failed: ${errMessage(err)}`),
  });
}
