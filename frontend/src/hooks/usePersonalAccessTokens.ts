/**
 * Personal Access Tokens queries + mutations.
 *
 * PATs let an external MCP client (Claude Code, Cline, Cursor, etc.) connect
 * to mcp.contacts.magicunicorn.dev as the issuing user. The MCP backend mints
 * a `co_pat_…` bearer that's shown exactly once on issuance; every subsequent
 * call returns only the last-4 suffix.
 *
 * The list query is auto-invalidated after issue/revoke so the Settings card
 * reflects the new row without a manual refetch.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { toast } from "sonner";
import { tools, MCPError, UnauthorizedError } from "@/lib/mcp";
import { userKey } from "@/lib/queryKeys";
import type { PersonalAccessTokenIssued, UUID } from "@/lib/types";

// PATs are scoped to the calling user (uc_uid), NOT the active workspace —
// they span workspaces — so this key is deliberately tenant-INDEPENDENT
// (`userKey`, the "g" sentinel) and survives a workspace-switch cache clear.
const PAT_QUERY_KEY = userKey.personalAccessTokens;

export function usePersonalAccessTokens() {
  const auth = useAuth();
  const token = auth.user?.access_token;
  return useQuery({
    queryKey: PAT_QUERY_KEY,
    enabled: Boolean(token),
    queryFn: async () => {
      try {
        return await tools.listPats(token ?? "");
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    }
  });
}

export type IssuePatArgs = {
  display_name: string;
  scopes?: string[];
  /** Number of days from now until expiry. Omit / null = never expires. */
  expires_in_days?: number | null;
};

/**
 * Mutation wrapping `issue_personal_access_token`. The returned plaintext is
 * NOT cached anywhere — the consumer captures it from `onSuccess` and renders
 * it in the amber callout, then drops it on dismiss.
 */
export function useIssuePatMutation() {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();

  return useMutation<PersonalAccessTokenIssued, Error, IssuePatArgs>({
    mutationFn: async (args) => {
      try {
        return await tools.issuePat(args, accessToken);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: PAT_QUERY_KEY });
      toast.success("Personal access token created.");
    },
    onError: (err) => {
      const msg = err instanceof MCPError ? err.message : err.message;
      toast.error(`Token issue failed: ${msg}`);
    }
  });
}

export function useRevokePatMutation() {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (args: { pat_id: UUID }) => {
      try {
        return await tools.revokePat(args, accessToken);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: PAT_QUERY_KEY });
      toast.success("Token revoked.");
    },
    onError: (err) => {
      const msg = err instanceof MCPError ? err.message : (err as Error).message;
      toast.error(`Revoke failed: ${msg}`);
    }
  });
}
