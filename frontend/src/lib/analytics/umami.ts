/**
 * Umami web-analytics loader — DORMANT unless VITE_UMAMI_SRC + VITE_UMAMI_WEBSITE_ID
 * are set, and only ever loaded AFTER explicit web-analytics consent.
 *
 * data-auto-track is OFF so the script never fires a pageview the instant it
 * loads (which would beat consent and capture query-string PII); pageviews are
 * tracked manually via trackPageview(), path-only (query strings stripped).
 */
import { env } from "@/lib/env";

const SCRIPT_ID = "co-umami-script";

function isConfigured(): boolean {
  return Boolean(env.umamiSrc && env.umamiWebsiteId);
}

export function loadUmami(): void {
  if (!isConfigured() || typeof document === "undefined") return;
  if (document.getElementById(SCRIPT_ID)) return; // already loaded

  const s = document.createElement("script");
  s.id = SCRIPT_ID;
  s.async = true;
  s.defer = true;
  s.src = env.umamiSrc;
  s.setAttribute("data-website-id", env.umamiWebsiteId);
  s.setAttribute("data-auto-track", "false");
  s.setAttribute("data-do-not-track", "true");
  document.head.appendChild(s);
}

export function unloadUmami(): void {
  if (typeof document === "undefined") return;
  document.getElementById(SCRIPT_ID)?.remove();
  delete window.umami;
}

/** Manual pageview — path only, never the query string (avoids PII leakage). */
export function trackPageview(path: string): void {
  if (!isConfigured()) return;
  try {
    window.umami?.track((props) => ({ ...props, url: path }));
  } catch {
    /* analytics must never throw into the app */
  }
}
