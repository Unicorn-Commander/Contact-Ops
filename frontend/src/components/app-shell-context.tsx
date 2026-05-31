/**
 * AppShellContext — lets descendant routes (rendered through AppShell's
 * <Outlet />) trigger the shell's shared overlays WITHOUT duplicating them.
 *
 * Today the AI Analyst right-rail and the ⌘K command palette are owned by
 * AppShell (it holds their open/mounted state and the single lazy-mounted
 * instance). The Dashboard's v2 command-center hero has its own "Ask the
 * Analyst" CTA that must open the *same* panel as the top-bar Sparkles button /
 * ⌘J — so AppShell publishes its `openAnalyst` (and `openPalette`) here and the
 * Dashboard consumes them. This guarantees one Analyst instance, one handler.
 *
 * `useAppShell()` is safe to call outside a provider (e.g. in a preview / test
 * render) — it returns no-op handlers so the surface degrades gracefully rather
 * than throwing.
 */
import { createContext, useContext } from "react";

export interface AppShellContextValue {
  /** Open the AI Contacts Analyst right-rail (same panel as ⌘J / the Sparkles button). */
  openAnalyst: () => void;
  /** Open the ⌘K command palette. */
  openPalette: () => void;
}

const noop = () => {};

const AppShellContext = createContext<AppShellContextValue>({
  openAnalyst: noop,
  openPalette: noop
});

export const AppShellProvider = AppShellContext.Provider;

export function useAppShell(): AppShellContextValue {
  return useContext(AppShellContext);
}
