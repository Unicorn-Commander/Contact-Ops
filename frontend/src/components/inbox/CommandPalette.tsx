/**
 * Command palette (Cmd+K).
 *
 * Built on cmdk. Wraps it in a shadcn-style Dialog overlay. The parent
 * controls open state (so the Cmd+K shortcut can toggle it).
 *
 * Commands cover the J/K/Y/N/H verbs (when a focused proposal exists),
 * jumps between needs-review / snoozed / resolved nav, Focus Mode
 * toggle, and filter shortcuts (HIPAA only, cross-tenant only, by agent).
 */
import { Command } from "cmdk";
import { useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export type CommandPaletteAction = {
  id: string;
  label: string;
  shortcut?: string;
  /** Group for visual separation. */
  group: "Actions" | "Navigate" | "Filter" | "View" | "Settings";
  onRun: () => void;
  /** Hide if the command isn't applicable in the current context. */
  hidden?: boolean;
};

export type CommandPaletteProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  actions: CommandPaletteAction[];
};

export function CommandPalette({ open, onOpenChange, actions }: CommandPaletteProps) {
  // cmdk handles keyboard nav internally; we just close on Escape.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onOpenChange(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  const visible = actions.filter((a) => !a.hidden);
  const grouped = visible.reduce<Record<string, CommandPaletteAction[]>>((acc, a) => {
    (acc[a.group] ||= []).push(a);
    return acc;
  }, {});
  const groupOrder: CommandPaletteAction["group"][] = [
    "Actions",
    "Navigate",
    "Filter",
    "View",
    "Settings",
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="overflow-hidden p-0 sm:max-w-xl">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <DialogDescription className="sr-only">
          Type to filter actions, then press Enter to run.
        </DialogDescription>
        <Command
          className="overflow-hidden"
          loop
          shouldFilter
          label="Command palette"
        >
          <Command.Input
            placeholder="Type a command or search…"
            className={cn(
              "w-full border-b bg-transparent px-4 py-3 text-sm outline-none placeholder:text-muted-foreground",
            )}
          />
          <Command.List className="max-h-96 overflow-y-auto p-2">
            <Command.Empty className="px-3 py-6 text-center text-sm text-muted-foreground">
              No matching commands.
            </Command.Empty>
            {groupOrder.map((group) => {
              const items = grouped[group];
              if (!items?.length) return null;
              return (
                <Command.Group key={group} heading={group} className="text-xs text-muted-foreground">
                  {items.map((a) => (
                    <Command.Item
                      key={a.id}
                      value={`${a.group} ${a.label}`}
                      onSelect={() => {
                        onOpenChange(false);
                        a.onRun();
                      }}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded px-3 py-2 text-sm",
                        "aria-selected:bg-violet-500/10 aria-selected:text-violet-500",
                      )}
                    >
                      <span className="flex-1">{a.label}</span>
                      {a.shortcut && (
                        <span className="font-mono text-[10px] text-muted-foreground">
                          {a.shortcut}
                        </span>
                      )}
                    </Command.Item>
                  ))}
                </Command.Group>
              );
            })}
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
