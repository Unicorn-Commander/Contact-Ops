/**
 * TypedPhraseConfirm — Resend-style modal for Tier-4 approvals.
 *
 * Built on shadcn Dialog (radix-ui/react-dialog). Modal contains:
 *   - Policy explanation ("This proposal touches HIPAA-tagged data…")
 *   - The phrase to type (mono font, copyable but not pre-filled)
 *   - Text input (case-insensitive whitespace-trimmed match per server B3)
 *   - [Cancel] [Confirm — irreversible without undo]
 *
 * After 3 wrong attempts: surfaces a "Did you mean to do this?" hint
 * with a link to snooze instead.
 */
import { useState } from "react";
import { AlertCircle } from "lucide-react";
import { phraseMatches } from "@/lib/inbox/tierSelector";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type TypedPhraseConfirmProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  expectedPhrase: string;
  /** Short explanation of what's about to happen. */
  policyExplanation: string;
  /** Optional "Why am I being asked to type?" expanded copy. */
  policyDetail?: string;
  busy?: boolean;
  onConfirm: (typed: string) => void;
  /** Provide a snooze handler so the misclick path can offer "Snooze instead". */
  onOfferSnooze?: () => void;
};

export function TypedPhraseConfirm({
  open,
  onOpenChange,
  expectedPhrase,
  policyExplanation,
  policyDetail,
  busy = false,
  onConfirm,
  onOfferSnooze,
}: TypedPhraseConfirmProps) {
  const [typed, setTyped] = useState("");
  const [attempts, setAttempts] = useState(0);
  const [shake, setShake] = useState(false);
  const [showDetail, setShowDetail] = useState(false);

  const matches = phraseMatches(typed, expectedPhrase);

  function handleConfirm() {
    if (!matches) {
      setAttempts((n) => n + 1);
      setShake(true);
      setTimeout(() => setShake(false), 400);
      return;
    }
    onConfirm(typed);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertCircle className="h-5 w-5 text-amber-500" />
            Confirm by typing the phrase below
          </DialogTitle>
          <DialogDescription className="pt-2">{policyExplanation}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="rounded-md border bg-muted/50 px-3 py-2">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Type exactly
            </p>
            <p className="select-all font-mono text-sm text-foreground">{expectedPhrase}</p>
          </div>
          <Input
            autoFocus
            spellCheck={false}
            autoComplete="off"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleConfirm();
              }
            }}
            disabled={busy}
            placeholder="Type the phrase above…"
            className={cn(
              "font-mono",
              shake && "animate-pulse border-rose-500",
            )}
            aria-invalid={!matches && typed.length > 0}
          />
          {policyDetail && (
            <button
              type="button"
              onClick={() => setShowDetail((v) => !v)}
              className="text-xs text-violet-500 hover:underline focus-visible:outline-none"
            >
              {showDetail ? "Hide" : "Why am I being asked to type?"}
            </button>
          )}
          {showDetail && policyDetail && (
            <p className="rounded border border-amber-500/30 bg-amber-50/40 dark:bg-amber-950/30 p-2 text-xs text-amber-900 dark:text-amber-200">
              {policyDetail}
            </p>
          )}
          {attempts >= 3 && (
            <div className="rounded border border-amber-500/30 bg-amber-50/40 dark:bg-amber-950/30 p-2 text-xs">
              <p className="text-amber-900 dark:text-amber-200">Did you mean to do this?</p>
              {onOfferSnooze && (
                <button
                  type="button"
                  onClick={() => {
                    onOpenChange(false);
                    onOfferSnooze();
                  }}
                  className="mt-1 text-violet-500 hover:underline"
                >
                  Snooze this proposal instead →
                </button>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            disabled={!matches || busy}
            onClick={handleConfirm}
            className="bg-violet-500 hover:bg-violet-600"
          >
            Confirm — irreversible without undo
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
