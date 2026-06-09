/**
 * SnoozePicker - DropdownMenu of quick snooze options + custom date.
 *
 * Quick options compute the snooze_until datetime client-side; "Custom
 * date" reveals an inline date input. (A future "Until X event" option will
 * pull from Project-Ops federation; it is omitted until that picker is built.)
 *
 * The `h` keypress on the focused proposal opens this picker via the
 * `forceOpen` prop wired from Inbox.tsx.
 */
import { useState } from "react";
import { Clock } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { KeyboardHint } from "@/design-system";

export type SnoozeReason =
  | "wait_for_event"
  | "tomorrow"
  | "end_of_week"
  | "next_monday"
  | "custom";

function tomorrowAt9(): Date {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(9, 0, 0, 0);
  return d;
}

function endOfWeek(): Date {
  // Next Friday 5pm
  const d = new Date();
  const day = d.getDay();
  // Sun=0...Sat=6; Friday=5. If already past Friday 5pm, go next Friday.
  const delta = (5 - day + 7) % 7 || 7;
  d.setDate(d.getDate() + delta);
  d.setHours(17, 0, 0, 0);
  return d;
}

function nextMondayAt9(): Date {
  const d = new Date();
  const day = d.getDay();
  const delta = (1 - day + 7) % 7 || 7;
  d.setDate(d.getDate() + delta);
  d.setHours(9, 0, 0, 0);
  return d;
}

export type SnoozePickerProps = {
  onSnooze: (until: Date, reason: SnoozeReason) => void;
  busy?: boolean;
};

export function SnoozePicker({
  onSnooze,
  busy = false,
}: SnoozePickerProps) {
  const [customMode, setCustomMode] = useState(false);
  const [customDate, setCustomDate] = useState("");

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-co-6" disabled={busy} title="Snooze">
          <Clock className="h-3.5 w-3.5" />
          Snooze
          <KeyboardHint keys="H" label="Snooze focused proposal" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64 rounded-[var(--radius-md)] border-border bg-popover shadow-[var(--shadow-3)]">
        <DropdownMenuLabel className="font-mono text-11 uppercase text-muted-foreground">Snooze until...</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => onSnooze(tomorrowAt9(), "tomorrow")}>
          Tomorrow 9:00 AM
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onSnooze(endOfWeek(), "end_of_week")}>
          End of week (Fri 5 PM)
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onSnooze(nextMondayAt9(), "next_monday")}>
          Next Monday 9:00 AM
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <div className="space-y-2 px-2 py-1.5">
          <button
            type="button"
            onClick={() => setCustomMode((v) => !v)}
            className="focus-ring rounded-[var(--radius-sm)] text-12 text-link hover:underline"
          >
            {customMode ? "Hide" : "Custom date..."}
          </button>
          {customMode && (
            <div className="flex gap-1">
              <Input
                type="datetime-local"
                value={customDate}
                onChange={(e) => setCustomDate(e.target.value)}
                className="h-8 text-xs"
              />
              <Button
                size="sm"
                disabled={!customDate}
                onClick={() => {
                  if (!customDate) return;
                  onSnooze(new Date(customDate), "custom");
                }}
              >
                OK
              </Button>
            </div>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
