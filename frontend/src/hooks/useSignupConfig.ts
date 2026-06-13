import { useQuery } from "@tanstack/react-query";
import { env } from "@/lib/env";

export interface SignupConfig {
  signup_enabled: boolean;
  signup_mode: "suite" | "standalone";
  suite_signup_url: string | null;
}

/**
 * Public, pre-auth fetch of the backend signup config. Drives whether the login
 * screen shows a signup CTA (and which mode). Backend-driven so flipping signup
 * on is a server env change, not an SPA rebuild. Fails safe to "disabled" (the
 * CTA simply doesn't render) so the login screen is never blocked by it.
 */
export function useSignupConfig() {
  return useQuery<SignupConfig>({
    queryKey: ["signup-config"],
    queryFn: async () => {
      const res = await fetch(`${env.mcpBaseUrl}/api/auth/signup-config`);
      if (!res.ok) throw new Error(`signup-config ${res.status}`);
      return (await res.json()) as SignupConfig;
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}
