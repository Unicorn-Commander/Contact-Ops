/**
 * Analytics consent — opt-IN, localStorage-persisted, GDPR-safe.
 *
 * Default state is "not decided, everything OFF" — no analytics fires until the
 * user explicitly accepts. The ConsentProvider also runs the consent-reactive
 * effect that loads/unloads the (dormant-unless-configured) Umami + PostHog
 * loaders, so consent is the single gate. Mirrors the localStorage pattern in
 * design-system/tokens.ts.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { loadUmami, unloadUmami } from "@/lib/analytics/umami";
import { loadPosthog, teardownPosthog } from "@/lib/analytics/posthog";

const STORAGE_KEY = "co.analytics.consent.v1";

export interface ConsentState {
  /** Website analytics (Umami). */
  web: boolean;
  /** Product analytics (PostHog). */
  product: boolean;
  /** Whether the user has answered the banner at all. */
  decided: boolean;
}

const DEFAULT_CONSENT: ConsentState = { web: false, product: false, decided: false };

function readConsent(): ConsentState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_CONSENT;
    const parsed = JSON.parse(raw) as Partial<ConsentState>;
    return {
      web: parsed.web === true,
      product: parsed.product === true,
      decided: parsed.decided === true
    };
  } catch {
    return DEFAULT_CONSENT;
  }
}

interface ConsentContextValue {
  consent: ConsentState;
  /** Persist a decision; any omitted field keeps its current value. `decided` is forced true. */
  setConsent: (next: Partial<Omit<ConsentState, "decided">>) => void;
}

const ConsentContext = createContext<ConsentContextValue | null>(null);

export function ConsentProvider({ children }: { children: React.ReactNode }) {
  const [consent, setConsentState] = useState<ConsentState>(() => readConsent());

  const setConsent = useCallback((next: Partial<Omit<ConsentState, "decided">>) => {
    setConsentState((prev) => {
      const merged: ConsentState = {
        web: next.web ?? prev.web,
        product: next.product ?? prev.product,
        decided: true
      };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
      } catch {
        /* ignore quota/availability errors — consent still applies in-memory */
      }
      return merged;
    });
  }, []);

  // Consent-reactive: (un)load each tracker as its bit flips. The loaders are
  // themselves dormant when their env is unset, so this is a no-op until both
  // consent AND configuration are present.
  useEffect(() => {
    if (consent.web) loadUmami();
    else unloadUmami();
  }, [consent.web]);

  useEffect(() => {
    if (consent.product) loadPosthog();
    else teardownPosthog();
  }, [consent.product]);

  const value = useMemo(() => ({ consent, setConsent }), [consent, setConsent]);
  return <ConsentContext.Provider value={value}>{children}</ConsentContext.Provider>;
}

export function useConsent(): ConsentContextValue {
  const ctx = useContext(ConsentContext);
  if (!ctx) throw new Error("useConsent must be used within a ConsentProvider");
  return ctx;
}
