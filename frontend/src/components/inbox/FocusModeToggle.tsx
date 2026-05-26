/**
 * FocusModeToggle - header switch + label.
 *
 * In Focus Mode the list filter raises the confidence floor to 0.90,
 * hides anything HIPAA-tagged, and inverts the default sort to
 * confidence-descending within auto-approval-eligible. Persists per-user.
 *
 * Wiring (consumed by Inbox.tsx):
 *   - `useFocusMode().enabled` is passed to `applyFocusMode`
 *   - The toggle button calls `toggle()` which writes to localStorage
 *     and broadcasts via the storage event (cross-tab sync).
 */
import { Focus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { KeyboardHint } from "@/design-system";

export type FocusModeToggleProps = {
  enabled: boolean;
  onToggle: () => void;
};

export function FocusModeToggle({ enabled, onToggle }: FocusModeToggleProps) {
  return (
    <Button
      variant={enabled ? "default" : "outline"}
      size="sm"
      onClick={onToggle}
      className={cn(
        "gap-co-6",
        enabled && "bg-primary text-primary-foreground hover:bg-primary/90",
      )}
      aria-pressed={enabled}
      title="Toggle Focus Mode (Command+.)"
    >
      <Focus className="h-3.5 w-3.5" aria-hidden="true" />
      <span>Focus Mode</span>
      <KeyboardHint
        keys="cmd+."
        label="Toggle Focus Mode"
        className={cn(
          enabled && "border-primary-foreground/30 bg-primary-foreground/10 text-primary-foreground",
        )}
      />
    </Button>
  );
}
