/**
 * PostHog product-analytics loader — DORMANT unless VITE_POSTHOG_KEY +
 * VITE_POSTHOG_HOST are set, and only ever capturing AFTER explicit product-
 * analytics consent.
 *
 * Loaded via the self-hosted snippet (array.js from the configured host) so
 * there's no npm dependency. PII-safe for a contacts product: autocapture +
 * session recording OFF, all text + attributes masked, opt_out_by_default true
 * (we opt-in only after consent). Identify by the OPAQUE uc_uid from the OIDC
 * token, never email/name; group by tenant_id.
 */
import { env } from "@/lib/env";

const SCRIPT_ID = "co-posthog-script";

function isConfigured(): boolean {
  return Boolean(env.posthogKey && env.posthogHost);
}

export function loadPosthog(): void {
  if (!isConfigured() || typeof document === "undefined") return;
  if (window.posthog?.__loaded) {
    window.posthog.opt_in_capturing();
    return;
  }
  if (document.getElementById(SCRIPT_ID)) return;

  const host = env.posthogHost.replace(/\/$/, "");
  const s = document.createElement("script");
  s.id = SCRIPT_ID;
  s.async = true;
  s.src = `${host}/static/array.js`;
  s.onload = () => {
    try {
      window.posthog?.init(env.posthogKey, {
        api_host: host,
        opt_out_capturing_by_default: true,
        autocapture: false,
        capture_pageview: false,
        disable_session_recording: true,
        persistence: "memory",
        person_profiles: "identified_only",
        mask_all_text: true,
        mask_all_element_attributes: true
      });
      window.posthog?.opt_in_capturing(); // consent already granted by the time we load
    } catch {
      /* analytics must never throw into the app */
    }
  };
  document.head.appendChild(s);
}

export function teardownPosthog(): void {
  try {
    window.posthog?.opt_out_capturing();
    window.posthog?.reset(true);
  } catch {
    /* ignore */
  }
}

/** Identify the current user by their opaque uc_uid + group by tenant. */
export function identifyPosthog(ucUid: string, tenantId?: string): void {
  if (!isConfigured() || !ucUid) return;
  try {
    window.posthog?.identify(ucUid);
    if (tenantId) window.posthog?.group("tenant", tenantId);
  } catch {
    /* ignore */
  }
}
