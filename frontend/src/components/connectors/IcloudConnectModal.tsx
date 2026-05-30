/**
 * IcloudConnectModal — Apple ID + app-specific password entry for the iCloud
 * connector. Posts to `configure_icloud_connector`. Backend validates the
 * credentials against Apple's CardDAV endpoint and returns either a success
 * envelope or a tool error with a user-facing reason.
 *
 * Form fields (verbatim with backend):
 *   - apple_id      : REMOVED / @me.com / @mac.com
 *   - app_password  : 16-char `xxxx-xxxx-xxxx-xxxx` (we don't enforce the
 *                     dashes; Apple shows it grouped but pastes work either way)
 *   - display_name  : free text — appears on the card & in the audit log
 *
 * UX:
 *   - Password input masks by default with a Show/Hide toggle (eye icon).
 *   - Help text links to appleid.apple.com (opens in a new tab + rel safe).
 *   - On invalid-credentials response, a red banner replaces the help line
 *     until the user edits the form.
 *   - Cancel closes the dialog and clears state; the parent owns the open flag
 *     and resets any local form state on subsequent opens.
 */
import { useEffect, useId, useState, type FormEvent } from "react";
import {
  AlertCircle,
  Cloud,
  Eye,
  EyeOff,
  ExternalLink,
  HelpCircle,
  KeyRound,
  Loader2
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface IcloudConnectFormValues {
  apple_id: string;
  app_password: string;
  display_name: string;
}

export interface IcloudConnectModalProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  onSubmit: (values: IcloudConnectFormValues) => Promise<unknown> | void;
  /** Drives the spinner + disables all inputs while the submit is in-flight. */
  isSubmitting?: boolean;
  /** Backend-formatted reason — surfaced verbatim in the red error banner. */
  errorMessage?: string | null;
}

const APPLE_HELP_URL = "https://appleid.apple.com/account/manage";
const HELP_PAGE_URL = "/help/connectors#icloud";

// ---- App-Specific Password disclosure --------------------------------------
//
// Native <details>/<summary> gives us free keyboard support and a built-in
// aria-expanded — we just style the chrome away. The 7-step list mirrors the
// canonical Apple flow exactly; copy lives here so the help page can pull
// the same text without drift.
function AppPasswordDisclosure() {
  return (
    <details
      className={cn(
        // Reduced-motion safe: only a max-height transition on the inner
        // content; the <details> open/close itself is instantaneous in CSS.
        "co-disclosure group rounded-md border border-border bg-background/40 text-xs"
      )}
    >
      <summary
        className={cn(
          "focus-ring flex cursor-pointer list-none items-center gap-2 rounded-md px-3 py-2 text-xs font-medium text-foreground",
          "hover:bg-muted/60"
        )}
      >
        <KeyRound
          className="h-3.5 w-3.5 text-[oklch(var(--co-sky-500))]"
          strokeWidth={1.8}
          aria-hidden="true"
        />
        <span className="flex-1">How do I get an app-specific password?</span>
        <span
          aria-hidden="true"
          className="text-muted-foreground transition-transform group-open:rotate-90 motion-reduce:transition-none"
        >
          ›
        </span>
      </summary>
      <div className="space-y-3 border-t border-border px-3 py-3 leading-snug text-muted-foreground">
        <div className="flex items-start gap-2 rounded-md border border-[oklch(var(--co-amber-500)/0.35)] bg-[oklch(var(--co-amber-500)/0.08)] px-2.5 py-2 text-[11px] leading-snug text-foreground">
          <AlertCircle
            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[oklch(var(--co-amber-500))]"
            strokeWidth={2}
            aria-hidden="true"
          />
          <span>
            Apple requires two-factor authentication enabled on your Apple ID before you can
            generate an app-specific password. If 2FA isn't enabled, the App-Specific Passwords
            menu won't appear on your account page.
          </span>
        </div>
        <ol className="list-decimal space-y-1.5 pl-4 text-[11px] leading-snug marker:text-muted-foreground">
          <li>
            Open{" "}
            <a
              href={APPLE_HELP_URL}
              target="_blank"
              rel="noreferrer noopener"
              className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
            >
              account.apple.com
              <ExternalLink
                className="ml-0.5 inline-block h-2.5 w-2.5"
                strokeWidth={2}
                aria-hidden="true"
              />
            </a>
            .
          </li>
          <li>Sign in with the Apple ID whose contacts you want to pull.</li>
          <li>
            Open <span className="font-medium text-foreground">Sign-In and Security</span> →{" "}
            <span className="font-medium text-foreground">App-Specific Passwords</span>.
          </li>
          <li>
            Click <span className="font-medium text-foreground">+ Generate an app-specific
            password</span>.
          </li>
          <li>
            Label it <span className="font-mono text-foreground">Contact-Ops</span> (or whatever
            you'll recognize).
          </li>
          <li>
            Apple will show a 16-character password formatted as{" "}
            <span className="font-mono text-foreground">xxxx-xxxx-xxxx-xxxx</span>.{" "}
            <span className="font-medium text-foreground">Copy it now</span> — same as
            Contact-Ops's own passwords, Apple only shows this once.
          </li>
          <li>Paste it into the field below.</li>
        </ol>
        <p className="text-[11px] leading-snug">
          The dashes are visual; pasting the password with or without dashes both work.
        </p>
        <p className="flex items-center gap-1 text-[11px]">
          <HelpCircle className="h-3 w-3" strokeWidth={1.8} aria-hidden="true" />
          <a
            href={HELP_PAGE_URL}
            className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
          >
            Open the full walkthrough &amp; troubleshooting
          </a>
        </p>
      </div>
    </details>
  );
}

// Tolerant: Apple emails come in a few flavours and corporate Managed Apple IDs
// have arbitrary suffixes. We only enforce the @-shape and let the backend say
// the real word on credential validity.
const APPLE_ID_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const PASSWORD_MIN_CHARS = 16;

function fieldFilled(value: string): boolean {
  return value.trim().length > 0;
}

export function IcloudConnectModal({
  open,
  onOpenChange,
  onSubmit,
  isSubmitting,
  errorMessage
}: IcloudConnectModalProps) {
  const appleIdId = useId();
  const passwordId = useId();
  const displayNameId = useId();

  const [appleId, setAppleId] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("iCloud");
  const [showPassword, setShowPassword] = useState(false);
  const [touched, setTouched] = useState({ appleId: false, password: false });

  // Reset the form whenever the dialog reopens — avoids stale credentials
  // hanging around if the user closed it mid-typing.
  useEffect(() => {
    if (open) {
      setAppleId("");
      setPassword("");
      setDisplayName("iCloud");
      setShowPassword(false);
      setTouched({ appleId: false, password: false });
    }
  }, [open]);

  const appleIdInvalid = touched.appleId && !APPLE_ID_RE.test(appleId.trim());
  const passwordTooShort =
    touched.password && password.replace(/-/g, "").length < PASSWORD_MIN_CHARS;
  const canSubmit =
    !isSubmitting &&
    APPLE_ID_RE.test(appleId.trim()) &&
    password.replace(/-/g, "").length >= PASSWORD_MIN_CHARS &&
    fieldFilled(displayName);

  // Temp diagnostic — surface what the modal actually sees on a Save click.
  // Mask the password value, only the length matters. Remove once we close
  // out the "Save does nothing" report.
  const debugSnapshot = () => ({
    canSubmit,
    isSubmitting,
    appleId_value: appleId,
    appleId_trimmed: appleId.trim(),
    appleId_regex_match: APPLE_ID_RE.test(appleId.trim()),
    password_length_raw: password.length,
    password_length_no_dashes: password.replace(/-/g, "").length,
    displayName_value: displayName,
    displayName_filled: fieldFilled(displayName)
  });

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    console.log("[iCloud Connect] Save clicked. State:", debugSnapshot());
    if (!canSubmit) {
      setTouched({ appleId: true, password: true });
      console.warn("[iCloud Connect] canSubmit=false → request NOT sent. Reasons above.");
      return;
    }
    console.log("[iCloud Connect] canSubmit=true → calling onSubmit() now…");
    void onSubmit({
      apple_id: appleId.trim(),
      app_password: password,
      display_name: displayName.trim() || "iCloud"
    });
  };

  // Inline "Save disabled because…" hint so it's obvious without DevTools.
  const disabledReasons: string[] = [];
  if (!APPLE_ID_RE.test(appleId.trim())) disabledReasons.push("Apple ID isn't a valid email shape");
  if (password.replace(/-/g, "").length < PASSWORD_MIN_CHARS) {
    const got = password.replace(/-/g, "").length;
    disabledReasons.push(`App-specific password needs 16 characters (after dashes); got ${got}`);
  }
  if (!fieldFilled(displayName)) disabledReasons.push("Display name can't be empty");
  if (isSubmitting) disabledReasons.push("A previous Save is still in flight");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <div className="flex items-center gap-2.5">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-[oklch(var(--co-sky-500)/0.15)] text-[oklch(var(--co-sky-500))]"
              aria-hidden="true"
            >
              <Cloud className="h-5 w-5" strokeWidth={1.8} />
            </span>
            <div>
              <DialogTitle className="text-base">Connect iCloud</DialogTitle>
              <DialogDescription className="mt-1 text-xs">
                Pull contacts from iCloud via CardDAV. Everything lands as proposals in your Review
                Queue.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          <div className="space-y-1.5">
            <label htmlFor={appleIdId} className="text-xs font-medium text-foreground">
              Apple ID
            </label>
            <Input
              id={appleIdId}
              type="email"
              autoComplete="email"
              spellCheck={false}
              autoCapitalize="off"
              placeholder="REMOVED"
              value={appleId}
              onChange={(event) => setAppleId(event.target.value)}
              onBlur={() => setTouched((prev) => ({ ...prev, appleId: true }))}
              disabled={isSubmitting}
              aria-invalid={appleIdInvalid || undefined}
              aria-describedby={appleIdInvalid ? `${appleIdId}-error` : undefined}
              required
            />
            {appleIdInvalid ? (
              <p
                id={`${appleIdId}-error`}
                className="text-[11px] text-[oklch(var(--co-rose-500))]"
              >
                Enter a valid email — Apple IDs are usually @icloud.com, @me.com, or @mac.com.
              </p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <label htmlFor={passwordId} className="flex items-center justify-between text-xs font-medium text-foreground">
              <span>App-specific password</span>
              <button
                type="button"
                onClick={() => setShowPassword((prev) => !prev)}
                className="focus-ring inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground"
                aria-pressed={showPassword}
                aria-label={showPassword ? "Hide password" : "Show password"}
                disabled={isSubmitting}
              >
                {showPassword ? (
                  <EyeOff className="h-3 w-3" strokeWidth={1.8} aria-hidden="true" />
                ) : (
                  <Eye className="h-3 w-3" strokeWidth={1.8} aria-hidden="true" />
                )}
                {showPassword ? "Hide" : "Show"}
              </button>
            </label>
            <Input
              id={passwordId}
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              spellCheck={false}
              autoCapitalize="off"
              placeholder="xxxx-xxxx-xxxx-xxxx"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              onBlur={() => setTouched((prev) => ({ ...prev, password: true }))}
              disabled={isSubmitting}
              aria-invalid={passwordTooShort || undefined}
              aria-describedby={passwordTooShort ? `${passwordId}-error` : `${passwordId}-help`}
              required
              className="font-mono"
            />
            {passwordTooShort ? (
              <p id={`${passwordId}-error`} className="text-[11px] text-[oklch(var(--co-rose-500))]">
                App-specific passwords are 16 characters (dashes optional).
              </p>
            ) : (
              <p
                id={`${passwordId}-help`}
                className={cn(
                  "text-[11px] leading-snug text-muted-foreground",
                  errorMessage && "hidden"
                )}
              >
                Generate at{" "}
                <a
                  href={APPLE_HELP_URL}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
                >
                  appleid.apple.com
                  <ExternalLink
                    className="ml-0.5 inline-block h-2.5 w-2.5"
                    strokeWidth={2}
                    aria-hidden="true"
                  />
                </a>{" "}
                → Sign-In and Security → App-Specific Passwords.
              </p>
            )}
          </div>

          <AppPasswordDisclosure />

          <div className="space-y-1.5">
            <label htmlFor={displayNameId} className="text-xs font-medium text-foreground">
              Display name
            </label>
            <Input
              id={displayNameId}
              type="text"
              autoComplete="off"
              placeholder="iCloud"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              disabled={isSubmitting}
            />
            <p className="text-[11px] text-muted-foreground">Shown on the card and in the audit log.</p>
          </div>

          {errorMessage ? (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-md border border-[oklch(var(--co-rose-500)/0.4)] bg-[oklch(var(--co-rose-500)/0.07)] px-3 py-2 text-xs leading-snug text-foreground"
            >
              <AlertCircle
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[oklch(var(--co-rose-500))]"
                strokeWidth={2}
                aria-hidden="true"
              />
              <span className="min-w-0">{errorMessage}</span>
            </div>
          ) : null}

          {/* Diagnostic: when Save is disabled, show why inline so the user
              never sees a silent no-op. Temporary — remove once Aaron has a
              working iCloud connector path. */}
          {!canSubmit && disabledReasons.length > 0 ? (
            <div className="rounded-md border border-[oklch(var(--co-amber-500)/0.4)] bg-[oklch(var(--co-amber-500)/0.08)] px-3 py-2 text-[11.5px] text-foreground">
              <p className="mb-1 font-medium">Save is disabled because:</p>
              <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
                {disabledReasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button type="submit" variant="gradient" disabled={!canSubmit}>
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.8} />
                  Connecting…
                </>
              ) : (
                <>
                  <Cloud className="h-4 w-4" strokeWidth={1.8} />
                  Connect iCloud
                </>
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
