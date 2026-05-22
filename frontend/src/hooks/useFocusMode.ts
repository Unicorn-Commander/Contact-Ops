/**
 * useFocusMode — persistent Focus Mode toggle.
 *
 * Persists via TanStack Query cache + localStorage. On mount the
 * localStorage value wins for first paint, then syncs to query cache.
 * Cross-tab sync via the storage event.
 */
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

const KEY = "contact-ops:focus-mode";

export function useFocusMode(): {
  enabled: boolean;
  toggle: () => void;
  set: (value: boolean) => void;
} {
  const queryClient = useQueryClient();
  const [enabled, setEnabled] = useState<boolean>(() => {
    try {
      return localStorage.getItem(KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    queryClient.setQueryData(["focus-mode"], enabled);
    try {
      localStorage.setItem(KEY, enabled ? "1" : "0");
    } catch {
      // ignore quota / private mode
    }
  }, [enabled, queryClient]);

  // Cross-tab sync: react to storage events from other tabs.
  useEffect(() => {
    function onStorage(ev: StorageEvent) {
      if (ev.key === KEY) {
        setEnabled(ev.newValue === "1");
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return {
    enabled,
    toggle: () => setEnabled((v) => !v),
    set: setEnabled,
  };
}
