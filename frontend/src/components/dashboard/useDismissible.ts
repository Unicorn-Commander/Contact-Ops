/**
 * Persisted dismiss state for a dashboard surface (e.g. the AI "at a glance"
 * card). Stores a single flag under a namespaced localStorage key so a
 * dismissal sticks across reloads. SSR/no-storage safe: falls back to in-memory
 * state and never throws.
 */
import { useCallback, useState } from "react";

const PREFIX = "co.dash.dismissed.";

function read(key: string): boolean {
  try {
    return window.localStorage.getItem(PREFIX + key) === "1";
  } catch {
    return false;
  }
}

export function useDismissible(key: string): { dismissed: boolean; dismiss: () => void; reset: () => void } {
  const [dismissed, setDismissed] = useState<boolean>(() => read(key));

  const dismiss = useCallback(() => {
    setDismissed(true);
    try {
      window.localStorage.setItem(PREFIX + key, "1");
    } catch {
      /* ignore — in-memory dismissal still applies for this session */
    }
  }, [key]);

  const reset = useCallback(() => {
    setDismissed(false);
    try {
      window.localStorage.removeItem(PREFIX + key);
    } catch {
      /* ignore */
    }
  }, [key]);

  return { dismissed, dismiss, reset };
}
