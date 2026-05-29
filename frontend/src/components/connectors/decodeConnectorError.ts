/**
 * decodeConnectorError — translate backend connector-error envelopes / OAuth
 * callback `?error=` codes into friendly, action-oriented messages for the UI.
 *
 * The connector pipeline (iCloud configure + OAuth start/callback for M365 +
 * Gmail) returns either a structured `ToolEnvelope` with a stable `code`
 * string, or — for OAuth callbacks — bounces back to /connectors with an
 * `?error=<CODE>` URL param. Both surfaces feed the same five well-known
 * code strings; anything else falls through to the raw message so we don't
 * accidentally hide a real bug behind a sanitised string.
 *
 * Known codes (kept stable with the backend):
 *   - INVALID_CREDENTIALS     (iCloud CardDAV configure)
 *   - OAUTH_STATE_MISMATCH    (OAuth callback — `state` doesn't match the
 *                              cookie the start URL stamped)
 *   - OAUTH_SCOPE_REJECTED    (OAuth callback — user un-ticked Contacts in
 *                              the provider's consent step)
 *   - OAUTH_REDIRECT_MISMATCH (provider rejected the registered callback URL)
 *
 * Public API is single-shot: `decodeConnectorError(input, opts?)`. The
 * provider hint chooses the correct console name in the REDIRECT_MISMATCH
 * sentence ("Azure portal" vs "Google Cloud Console").
 */
import type { ConnectorProvider } from "@/lib/types";

/** Provider hint feeds the REDIRECT_MISMATCH message. iCloud doesn't OAuth,
 *  but accepting it keeps the call-site symmetric. */
export type DecodeProvider = ConnectorProvider;

export interface DecodeConnectorErrorOptions {
  /** Hint used to swap "Azure portal" / "Google Cloud Console" into the
   *  REDIRECT_MISMATCH copy. Defaults to a generic phrasing. */
  provider?: DecodeProvider;
}

/** Input may be an Error, a structured envelope, a string code, or a
 *  parsed callback query (e.g. `{ error: "OAUTH_STATE_MISMATCH" }`). */
export type DecodeConnectorErrorInput =
  | Error
  | string
  | null
  | undefined
  | {
      code?: string | null;
      message?: string | null;
      error?: string | null;
      error_description?: string | null;
    };

const KNOWN_CODES = [
  "INVALID_CREDENTIALS",
  "OAUTH_STATE_MISMATCH",
  "OAUTH_SCOPE_REJECTED",
  "OAUTH_REDIRECT_MISMATCH"
] as const;

type KnownCode = (typeof KNOWN_CODES)[number];

function extractRawCode(input: DecodeConnectorErrorInput): string | null {
  if (!input) return null;
  if (typeof input === "string") return input;
  if (input instanceof Error) {
    // The backend wrapper formats errors as "CODE: message" sometimes; we
    // grab whichever known code appears in the message.
    const text = input.message ?? "";
    const upper = text.toUpperCase();
    for (const code of KNOWN_CODES) {
      if (upper.includes(code)) return code;
    }
    return null;
  }
  // Envelope shape.
  return input.code ?? input.error ?? null;
}

function isKnownCode(value: string | null | undefined): value is KnownCode {
  if (!value) return false;
  const normalized = value.toUpperCase().trim();
  return (KNOWN_CODES as readonly string[]).includes(normalized);
}

function consoleNameFor(provider?: DecodeProvider): string {
  if (provider === "m365") return "Azure portal";
  if (provider === "gmail") return "Google Cloud Console";
  return "the provider's developer console";
}

function providerScopeName(provider?: DecodeProvider): string {
  if (provider === "m365") return "Microsoft";
  if (provider === "gmail") return "Google";
  return "the provider";
}

const MESSAGES: Record<KnownCode, (opts: DecodeConnectorErrorOptions) => string> = {
  INVALID_CREDENTIALS: () =>
    "Apple rejected those credentials. Confirm 2FA is enabled and that the password came from the App-Specific Passwords page, not your real Apple ID password.",
  OAUTH_STATE_MISMATCH: () =>
    "Sign-in took too long or another browser window finished it. Please click Connect again.",
  OAUTH_SCOPE_REJECTED: (opts) =>
    `${providerScopeName(opts.provider) === "the provider" ? "Microsoft/Google" : providerScopeName(opts.provider)} didn't grant access to contacts. Click Connect again and tick the contacts permission.`,
  OAUTH_REDIRECT_MISMATCH: (opts) =>
    `The provider rejected the callback URL. Contact-Ops admin needs to verify the OAuth app's redirect URI in ${consoleNameFor(opts.provider)}.`
};

/**
 * Map an error input to a friendly, human-facing message.
 *
 * Returns `null` only when the input itself is empty (so call-sites can
 * conditionally hide the banner). When a known code matches we return the
 * mapped copy; otherwise we fall back to the raw `.message` so a real bug
 * is never silently swallowed.
 */
export function decodeConnectorError(
  input: DecodeConnectorErrorInput,
  options: DecodeConnectorErrorOptions = {}
): string | null {
  if (!input) return null;
  const raw = extractRawCode(input);
  if (isKnownCode(raw)) {
    return MESSAGES[raw.toUpperCase().trim() as KnownCode](options);
  }
  // Fall through: keep the raw message verbatim so genuine bugs surface.
  if (typeof input === "string") return input;
  if (input instanceof Error) return input.message || null;
  return input.message ?? input.error_description ?? input.error ?? null;
}

/** Predicate helper — useful when callers want to know if they're dealing
 *  with a known code (e.g. to keep the rose error banner vs. a softer tint). */
export function isKnownConnectorErrorCode(input: DecodeConnectorErrorInput): boolean {
  const raw = extractRawCode(input);
  return isKnownCode(raw);
}
