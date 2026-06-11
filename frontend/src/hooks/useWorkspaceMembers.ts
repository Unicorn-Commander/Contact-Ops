/**
 * Workspace members (Team) queries + mutations.
 *
 * The members of the ACTIVE workspace (its user_tenant_membership rows), managed
 * from Settings → Members by a workspace admin. The list query is tenant-scoped
 * (it changes with the active workspace) and auto-invalidated after every
 * invite / role-change / revoke so the card stays live.
 *
 * Authority is enforced server-side (workspace admin). A non-admin caller's
 * list query fails with INSUFFICIENT_ROLE; `useWorkspaceMembers().error` carries
 * that MCPError so the UI can render an admin-only notice instead of the table.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { toast } from "sonner";
import { MCPError, UnauthorizedError, tools } from "@/lib/mcp";
import { useQueryKeys } from "@/hooks/useMcp";
import type { WorkspaceRole } from "@/lib/types";

export function useWorkspaceMembers(includeInactive = false) {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const qk = useQueryKeys();
  return useQuery({
    queryKey: [...qk.workspaceMembers, { includeInactive }] as const,
    enabled: Boolean(token),
    // A non-admin gets INSUFFICIENT_ROLE every time — don't hammer it.
    retry: false,
    queryFn: async () => {
      try {
        return await tools.listWorkspaceMembers(token ?? "", {
          include_inactive: includeInactive
        });
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    }
  });
}

function errMessage(err: unknown): string {
  if (err instanceof MCPError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function useInviteMemberMutation() {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: { email: string; role?: WorkspaceRole }) => {
      try {
        return await tools.inviteWorkspaceMember(args, accessToken);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: qk.workspaceMembers });
      toast.success(
        res.created ? `Added ${res.email} to the workspace.` : `Updated ${res.email}.`
      );
    },
    onError: (err) => toast.error(`Invite failed: ${errMessage(err)}`)
  });
}

export function useUpdateMemberRoleMutation() {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: { user_uc_uid: string; role: WorkspaceRole }) => {
      try {
        return await tools.updateWorkspaceMemberRole(args, accessToken);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.workspaceMembers });
      toast.success("Role updated.");
    },
    onError: (err) => toast.error(`Role change failed: ${errMessage(err)}`)
  });
}

export function useRevokeMemberMutation() {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: { user_uc_uid: string }) => {
      try {
        return await tools.revokeWorkspaceMember(args, accessToken);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.workspaceMembers });
      toast.success("Member removed.");
    },
    onError: (err) => toast.error(`Remove failed: ${errMessage(err)}`)
  });
}
