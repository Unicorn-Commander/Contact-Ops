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
import { AgentBadge, KeyboardHint } from "@/design-system";

export type CommandPaletteAction = {
  id: string;
  label: string;
  shortcut?: string;
  agentSlug?: string;
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
      <DialogContent className="overflow-hidden rounded-[var(--radius-lg)] border-border bg-popover p-0 shadow-[var(--shadow-4)] sm:max-w-xl">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <DialogDescription className="sr-only">
          Type to filter actions, then press Enter to run.
        </DialogDescription>
        <Command
          className="overflow-hidden bg-popover"
          loop
          shouldFilter
          label="Command palette"
        >
          <Command.Input
            placeholder="Type a command or search..."
            className={cn(
              "w-full border-b border-border bg-transparent px-co-16 py-co-12 text-14 outline-none placeholder:text-muted-foreground",
            )}
          />
          <Command.List className="co-scrollbar max-h-96 overflow-y-auto p-co-8">
            <Command.Empty className="px-co-12 py-co-24 text-center text-13 text-muted-foreground">
              No matching commands.
            </Command.Empty>
            {groupOrder.map((group) => {
              const items = grouped[group];
              if (!items?.length) return null;
              return (
                <Command.Group
                  key={group}
                  heading={group}
                  className="pb-co-4 text-12 text-muted-foreground [&_[cmdk-group-heading]]:px-co-8 [&_[cmdk-group-heading]]:pb-co-4 [&_[cmdk-group-heading]]:pt-co-8 [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-11 [&_[cmdk-group-heading]]:uppercase"
                >
                  {items.map((a) => (
                    <Command.Item
                      key={a.id}
                      value={`${a.group} ${a.label}`}
                      onSelect={() => {
                        onOpenChange(false);
                        a.onRun();
                      }}
                      className={cn(
                        "flex cursor-pointer items-center gap-co-8 rounded-[var(--radius-sm)] px-co-8 py-co-8 text-13 text-foreground",
                        "aria-selected:bg-primary/10 aria-selected:text-link",
                      )}
                    >
                      <span className="flex flex-1 items-center gap-co-6">
                        {a.agentSlug ? (
                          <>
                            <span>Filter by</span>
                            <AgentBadge slug={a.agentSlug} size="xs" />
                          </>
                        ) : (
                          a.label
                        )}
                      </span>
                      {a.shortcut && (
                        <KeyboardHint keys={a.shortcut} label={`${a.label} shortcut`} />
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
