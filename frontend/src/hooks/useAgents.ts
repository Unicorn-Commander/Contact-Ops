/**
 * Agent Command Center queries + mutations (backend mcp/tools/agent_admin.py).
 *
 * Surfaces CO's agent-governance backend: the workspace fleet kill-switch
 * (get_agent_governance / set_agents_paused), the agent registry (list_agents),
 * per-agent calibration state (get_agent_trust), manual tier overrides
 * (promote/demote_agent_tier), and the per-agent circuit breaker
 * (pause/resume_agent).
 *
 * Every tool is ADMIN-role + "contactops:agents.admin" scope, enforced
 * SERVER-SIDE. A non-admin caller's governance read fails with a role error;
 * `useAgentGovernance().error` carries that MCPError so the console can render an
 * admin-only notice instead of the controls (same pattern as workspace members).
 * Mutations own their own sonner toasts and invalidate the agent keys so the
 * banner / roster stay live.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { toast } from "sonner";
import { MCPError, UnauthorizedError, tools } from "@/lib/mcp";
import { useQueryKeys } from "@/hooks/useMcp";

/** Visibility scope the console reads/writes trust for; CO defaults to private. */
export type AgentVisibility = "private" | "team" | "org" | "shared";

function errMessage(err: unknown): string {
  if (err instanceof MCPError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

/**
 * True when the error is a role/scope denial (the caller is not an admin). The
 * backend's structured envelope uses FORBIDDEN_ROLE / FORBIDDEN_SCOPE; the
 * workspace-members surface also sees INSUFFICIENT_ROLE, so we treat all three
 * as "not an admin" for the gate notice.
 */
export function isRoleDenied(err: unknown): boolean {
  return (
    err instanceof MCPError &&
    (err.code === "FORBIDDEN_ROLE" ||
      err.code === "FORBIDDEN_SCOPE" ||
      err.code === "INSUFFICIENT_ROLE")
  );
}

export function useAgents(includeInactive = false) {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const qk = useQueryKeys();
  return useQuery({
    queryKey: [...qk.agents, { includeInactive }] as const,
    enabled: Boolean(token),
    queryFn: () => tools.listAgents(token ?? "", { include_inactive: includeInactive })
  });
}

export function useAgentGovernance() {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const qk = useQueryKeys();
  return useQuery({
    queryKey: qk.agentGovernance,
    enabled: Boolean(token),
    // A non-admin gets a role denial every time; don't hammer it.
    retry: false,
    queryFn: async () => {
      try {
        return await tools.getAgentGovernance(token ?? "");
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    }
  });
}

/**
 * Per-agent calibration. Mounted once per roster card. `found: false` (or a null
 * `trust`) is a NORMAL state, not an error: the agent has not been calibrated in
 * this workspace yet. A role denial throws so the page can branch on it once.
 */
export function useAgentTrust(slug: string, visibility: AgentVisibility = "private") {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const qk = useQueryKeys();
  return useQuery({
    queryKey: [...qk.agentTrust(slug), visibility] as const,
    enabled: Boolean(token) && Boolean(slug),
    retry: false,
    queryFn: async () => {
      try {
        return await tools.getAgentTrust({ agent_slug: slug, visibility }, token ?? "");
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    }
  });
}

/** Pause or resume the WHOLE fleet (the kill-switch banner). */
export function useSetAgentsPausedMutation() {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: { paused: boolean; reason: string }) => {
      try {
        return await tools.setAgentsPaused(args, accessToken);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: qk.agents });
      toast.success(res.agents_paused ? "All agents paused." : "Agents resumed.");
    },
    onError: (err) => toast.error(`Could not update the fleet: ${errMessage(err)}`)
  });
}

/** Promote one agent's stored trust tier up by one (manual override). */
export function usePromoteAgentMutation() {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: {
      agent_slug: string;
      reason: string;
      visibility?: AgentVisibility;
    }) => {
      try {
        return await tools.promoteAgentTier(args, accessToken);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: qk.agents });
      if (res.changed) {
        toast.success(`Promoted to ${res.new_tier_label}.`);
      } else {
        toast.info(`Already at the top tier (${res.new_tier_label}).`);
      }
    },
    onError: (err) => toast.error(`Promote failed: ${errMessage(err)}`)
  });
}

/** Demote one agent's stored trust tier down by one (manual override). */
export function useDemoteAgentMutation() {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: {
      agent_slug: string;
      reason: string;
      visibility?: AgentVisibility;
    }) => {
      try {
        return await tools.demoteAgentTier(args, accessToken);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: qk.agents });
      if (res.changed) {
        toast.success(`Demoted to ${res.new_tier_label}.`);
      } else {
        toast.info(`Already at the bottom tier (${res.new_tier_label}).`);
      }
    },
    onError: (err) => toast.error(`Demote failed: ${errMessage(err)}`)
  });
}

/** Open one agent's circuit breaker (pause just that agent). */
export function usePauseAgentMutation() {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: { agent_slug: string; reason: string }) => {
      try {
        return await tools.pauseAgent(args, accessToken);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.agents });
      toast.success("Agent paused.");
    },
    onError: (err) => toast.error(`Pause failed: ${errMessage(err)}`)
  });
}

/** Close one agent's circuit breaker (resume just that agent). */
export function useResumeAgentMutation() {
  const auth = useAuth();
  const accessToken = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation({
    mutationFn: async (args: { agent_slug: string; reason: string }) => {
      try {
        return await tools.resumeAgent(args, accessToken);
      } catch (err) {
        if (err instanceof UnauthorizedError) void auth.signinRedirect();
        throw err;
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk.agents });
      toast.success("Agent resumed.");
    },
    onError: (err) => toast.error(`Resume failed: ${errMessage(err)}`)
  });
}
