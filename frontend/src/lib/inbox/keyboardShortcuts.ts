/**
 * Linear-style keyboard bindings for the inbox.
 *
 * All bindings scope to "inbox" — the HotkeysProvider in App.tsx scopes
 * to the active route. Outside `/inbox`, none of these fire. Inputs +
 * contenteditable opt out via react-hotkeys-hook's built-in behavior.
 */
import { useHotkeys } from "react-hotkeys-hook";

export const INBOX_HOTKEY_SCOPE = "inbox";

export type InboxHotkey =
  | "j"
  | "k"
  | "e"
  | "y"
  | "n"
  | "h"
  | "m"
  | "d"
  | "esc"
  | "select-all"
  | "command-palette"
  | "focus-mode"
  | "undo"
  | "extend-down"
  | "extend-up"
  | "toggle-select";

export type InboxHotkeyHandlers = {
  [K in InboxHotkey]?: () => void;
};

/**
 * Bind the canonical inbox shortcuts. Pass only the handlers you've
 * implemented; missing keys are silently no-op.
 *
 * Why react-hotkeys-hook: it handles scope, contenteditable opt-out,
 * and modifier composition. Rolling our own useEffect keydown gets
 * those edge cases wrong.
 */
export function useInboxShortcuts(handlers: InboxHotkeyHandlers): void {
  const bind = (
    key: string,
    handler: (() => void) | undefined,
    deps: unknown[] = [],
  ) => {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    useHotkeys(
      key,
      (e) => {
        if (!handler) return;
        e.preventDefault();
        handler();
      },
      {
        scopes: INBOX_HOTKEY_SCOPE,
        enableOnFormTags: false,
        enableOnContentEditable: false,
      },
      deps,
    );
  };

  bind("j", handlers.j, [handlers.j]);
  bind("k", handlers.k, [handlers.k]);
  bind("e", handlers.e, [handlers.e]);
  bind("y", handlers.y, [handlers.y]);
  bind("n", handlers.n, [handlers.n]);
  bind("h", handlers.h, [handlers.h]);
  bind("m", handlers.m, [handlers.m]);
  bind("d", handlers.d, [handlers.d]);
  bind("escape", handlers.esc, [handlers.esc]);
  bind("mod+a", handlers["select-all"], [handlers["select-all"]]);
  bind("mod+k", handlers["command-palette"], [handlers["command-palette"]]);
  bind("mod+.", handlers["focus-mode"], [handlers["focus-mode"]]);
  bind("mod+z, u", handlers.undo, [handlers.undo]);
  // Range-extend the multi-selection from the anchor (Linear/Gmail cadence).
  bind("shift+j, shift+down", handlers["extend-down"], [handlers["extend-down"]]);
  bind("shift+k, shift+up", handlers["extend-up"], [handlers["extend-up"]]);
  // Toggle the focused row into the multi-select set without opening it.
  bind("x", handlers["toggle-select"], [handlers["toggle-select"]]);
}

export type InboxShortcutDescription = {
  key: string;
  description: string;
};

/**
 * For the help screen / command palette help section.
 */
export const INBOX_SHORTCUT_DESCRIPTIONS: InboxShortcutDescription[] = [
  { key: "J", description: "Next proposal" },
  { key: "K", description: "Previous proposal" },
  { key: "E", description: "Expand detail" },
  { key: "Y", description: "Approve focused" },
  { key: "N", description: "Reject focused" },
  { key: "H", description: "Snooze focused" },
  { key: "M", description: "Mute agent for this entity" },
  { key: "D", description: "Dismiss as duplicate" },
  { key: "X", description: "Add focused to selection" },
  { key: "⇧J / ⇧K", description: "Extend selection down / up" },
  { key: "Esc", description: "Clear selection / close detail" },
  { key: "⌘A", description: "Select all visible" },
  { key: "⌘K", description: "Command palette" },
  { key: "⌘.", description: "Toggle Focus Mode" },
  { key: "⌘Z / U", description: "Undo last decision" },
];
