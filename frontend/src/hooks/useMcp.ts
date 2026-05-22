import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { UnauthorizedError, tools } from "@/lib/mcp";

export function useAccessToken() {
  const auth = useAuth();
  return auth.user?.access_token ?? "";
}

function useAuthedQuery<T>(key: unknown[], queryFn: (token: string) => Promise<T>, enabled = true) {
  const auth = useAuth();
  const token = auth.user?.access_token;
  return useQuery({
    queryKey: key,
    enabled: Boolean(token) && enabled,
    queryFn: async () => {
      try {
        return await queryFn(token ?? "");
      } catch (error) {
        if (error instanceof UnauthorizedError) void auth.signinRedirect();
        throw error;
      }
    }
  });
}

export const useToolsList = () => useAuthedQuery(["tools"], tools.list);
export const useDashboard = () => useAuthedQuery(["dashboard"], tools.dashboard);
export const useRecentEvents = () => useAuthedQuery(["events", "recent"], tools.recentEvents);
export const usePeople = (args: Record<string, unknown>) =>
  useAuthedQuery(["people", args], (token) => (args.query ? tools.searchPeople(args, token) : tools.listPeople(args, token)));
export const usePerson = (id: string) => useAuthedQuery(["person", id], (token) => tools.getPerson(id, token), Boolean(id));
export const useOrganizations = (args: Record<string, unknown>) =>
  useAuthedQuery(["orgs", args], (token) =>
    args.query ? tools.searchOrganizations(args, token) : tools.listOrganizations(args, token)
  );
export const useOrganization = (id: string) =>
  useAuthedQuery(["org", id], (token) => tools.getOrganization(id, token), Boolean(id));
export const useTags = () => useAuthedQuery(["tags"], tools.listTags);
export const useTenants = () => useAuthedQuery(["tenants"], tools.listTenants);
export const usePhotos = (personId: string) => useAuthedQuery(["photos", personId], (token) => tools.listPhotos(personId, token));
export const useCardDavPasswords = () => useAuthedQuery(["carddav-passwords"], tools.listCardDavPasswords);

export function useMcpMutation<TArgs extends Record<string, unknown>>(mutationFn: (args: TArgs, token: string) => Promise<unknown>) {
  const token = useAccessToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: TArgs) => mutationFn(args, token),
    onSuccess: async () => {
      await queryClient.invalidateQueries();
    }
  });
}
