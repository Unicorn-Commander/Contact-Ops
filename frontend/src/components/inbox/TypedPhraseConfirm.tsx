/**
 * TypedPhraseConfirm - Resend-style modal for Tier-4 approvals.
 *
 * Built on shadcn Dialog (radix-ui/react-dialog). Modal contains:
 *   - Policy explanation ("This proposal touches HIPAA-tagged data...")
 *   - The phrase to type (mono font, copyable but not pre-filled)
 *   - Text input (case-insensitive whitespace-trimmed match per server B3)
 *   - [Cancel] [Confirm, irreversible without undo]
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
import { KeyboardHint, Spinner } from "@/design-system";

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
      <DialogContent className="rounded-[var(--radius-lg)] border-border bg-popover shadow-[var(--shadow-4)]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertCircle className="h-5 w-5 text-warning" />
            Confirm by typing the phrase below
          </DialogTitle>
          <DialogDescription className="pt-2">{policyExplanation}</DialogDescription>
        </DialogHeader>
        <div className="space-y-co-12">
          <div className="rounded-md border border-border bg-muted/50 px-co-12 py-co-8">
            <p className="font-mono text-11 uppercase text-muted-foreground">
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
            placeholder="Type the phrase above..."
            className={cn(
              "font-mono",
              shake && "animate-pulse border-destructive",
            )}
            aria-invalid={!matches && typed.length > 0}
          />
          {policyDetail && (
            <button
              type="button"
              onClick={() => setShowDetail((v) => !v)}
              className="focus-ring rounded-[var(--radius-sm)] text-12 text-link hover:underline"
            >
              {showDetail ? "Hide" : "Why am I being asked to type?"}
            </button>
          )}
          {showDetail && policyDetail && (
            <p className="rounded-md border border-warning/35 bg-warning/10 p-co-8 text-12 text-foreground">
              {policyDetail}
            </p>
          )}
          {attempts >= 3 && (
            <div className="rounded-md border border-warning/35 bg-warning/10 p-co-8 text-12">
              <p className="text-foreground">Did you mean to do this?</p>
              {onOfferSnooze && (
                <button
                  type="button"
                  onClick={() => {
                    onOpenChange(false);
                    onOfferSnooze();
                  }}
                  className="focus-ring mt-co-4 rounded-[var(--radius-sm)] text-link hover:underline"
                >
                  Snooze this proposal instead
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
            className="bg-primary text-primary-foreground hover:bg-primary/90"
          >
            {busy ? <Spinner size="sm" label="" /> : null}
            Confirm
            <KeyboardHint
              keys="Enter"
              label="Confirm typed phrase"
              className="border-primary-foreground/30 bg-primary-foreground/10 text-primary-foreground"
            />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
