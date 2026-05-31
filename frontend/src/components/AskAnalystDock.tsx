/**
 * AskAnalystDock — the docked, floating "Ask the Analyst" launcher.
 * ================================================================
 * A subtle glass pill anchored bottom-right that opens the SAME AI Contacts
 * Analyst right-rail as the top-bar "Ask AI ⌘J" pill and the ⌘J hotkey. It is a
 * second, more discoverable entry into the single shell-owned Analyst panel — it
 * never owns or duplicates the panel, it only calls the handler it's given.
 *
 * Behaviour (per the v2 showcase, section 5 "docked launcher"):
 *   - bottom-right, fixed, above content but below the panel/palette overlays.
 *   - HIDDEN while the panel is open (`hidden` prop) so the launcher never sits
 *     under / fights the open rail.
 *   - dismissible — an X collapses it for the session; once dismissed it stays
 *     gone (the ⌘J hotkey and the top-bar pill remain, so the Analyst is never
 *     unreachable). Dismissal is intentionally session-local, not persisted.
 *   - reduced-motion safe — the hover-bloom lift is gated by .co-v2-hover-bloom
 *     (disabled under prefers-reduced-motion in tokens-v2.css) and the
 *     mount/dismiss fade is skipped entirely when the user prefers reduced motion.
 *   - does not cover critical UI — it's a small pill in the corner with generous
 *     inset, and it yields (unmounts) whenever the panel is open.
 *
 * Accessibility: the launcher is a real <button> with an aria-label and
 * aria-keyshortcuts; the dismiss control is a separate labelled button so the two
 * actions are independently operable by keyboard / AT.
 */
import { useEffect, useState } from "react";
import { Wand2, X } from "lucide-react";
import { cn } from "@/lib/utils";

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export interface AskAnalystDockProps {
  /** Open the shared AI Contacts Analyst right-rail (same handler as ⌘J / the top-bar pill). */
  onOpen: () => void;
  /** True while the Analyst panel is open — the dock hides so it never sits under the rail. */
  hidden?: boolean;
}

export function AskAnalystDock({ onOpen, hidden = false }: AskAnalystDockProps) {
  const [dismissed, setDismissed] = useState(false);
  // Skip the entrance fade for reduced-motion users (and read once on mount).
  const reduce = prefersReducedMotion();
  const [entered, setEntered] = useState(reduce);

  useEffect(() => {
    if (reduce) return;
    // Next frame so the transition runs from the initial (faded) state.
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, [reduce]);

  // Hide while the panel is open or after the user dismisses it. The ⌘J hotkey
  // and the top-bar "Ask AI" pill remain, so the Analyst is still reachable.
  if (hidden || dismissed) return null;

  return (
    <div
      className={cn(
        // Sit above page content but BELOW the Analyst rail / command palette
        // (those are Radix overlays at higher stacking contexts). Generous inset
        // so it never crowds page actions; nudged up on small screens.
        "fixed bottom-4 right-4 z-30 sm:bottom-6 sm:right-6",
        !reduce && "transition-opacity duration-300 ease-out",
        entered ? "opacity-100" : "opacity-0"
      )}
    >
      <div className="co-v2-hover-bloom co-v2-glass-calm group relative flex items-center rounded-full py-2 pl-2.5 pr-2">
        <button
          type="button"
          onClick={onOpen}
          aria-label="Ask the Analyst — find anyone, summarize, or dedupe your contacts"
          aria-keyshortcuts="Meta+J Control+J"
          className="focus-ring flex items-center gap-2.5 rounded-full pr-1.5 text-left"
        >
          <span
            className="co-v2-glow-strong bg-gradient-brand flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-primary-foreground"
            aria-hidden="true"
          >
            <Wand2 className="h-4 w-4" strokeWidth={2} />
          </span>
          <span className="hidden pr-1 sm:block">
            <span className="block text-[13px] font-semibold leading-tight text-foreground">Ask the Analyst</span>
            <span className="block text-[11px] leading-tight text-muted-foreground">
              Find anyone, summarize, dedupe…
            </span>
          </span>
        </button>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          aria-label="Dismiss the Ask the Analyst launcher"
          className="focus-ring ml-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" strokeWidth={2} />
        </button>
      </div>
    </div>
  );
}
