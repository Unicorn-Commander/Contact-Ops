import { z } from "zod";

import { getActiveEngine, type ChatMessage } from "@/lib/ondevice/llm";
import type { StructuredContact } from "@/lib/ondevice/db";

const GENERIC_EMAIL_PREFIXES = ["info@", "contact@", "hello@", "support@", "sales@", "admin@"] as const;

const SYSTEM_PROMPT = `You are an expert contact extraction system. Extract contact information from business cards, pasted blurbs, signatures, website snippets, and profile notes.

RULES:
1. Extract real people with names when available.
2. Match emails and phones to the correct person when multiple are listed.
3. Infer title and organization from context.
4. Keep notes short and factual.
5. Return valid JSON only, no markdown or extra text.

OUTPUT FORMAT:
{"contacts":[{"name":"Full Name","emails":["REMOVED"],"phones":["(555) 123-4567"],"org":"Company","title":"Owner","notes":"short context"}]}

EXAMPLES:

Input: "Meet John Smith, founder at Acme. Email REMOVED. Call us at (555) 111-2222."
Output: {"contacts":[{"name":"John Smith","emails":["REMOVED"],"phones":[],"org":"Acme","title":"Founder","notes":""}]}

Input: "Owner: Mary Johnson - REMOVED (555) 333-4444. Office Manager: Bob Lee - REMOVED"
Output: {"contacts":[{"name":"Mary Johnson","emails":["REMOVED"],"phones":["(555) 333-4444"],"org":"","title":"Owner","notes":""},{"name":"Bob Lee","emails":["REMOVED"],"phones":[],"org":"","title":"Office Manager","notes":""}]}

Input: "Contact us at REMOVED or call (555) 999-0000"
Output: {"contacts":[]}`;

/**
 * Human-readable description of the contract the model must satisfy. Appended to
 * the single repair re-prompt so the model can self-correct. Kept terse on
 * purpose: small on-device models follow short, concrete shapes better than long
 * schema dumps.
 */
const SHAPE_HINT =
  '{"contacts":[{"name":string,"emails":string[],"phones":string[],"org"?:string,"title"?:string,"notes"?:string}]}';

/**
 * Lenient zod schema for the *raw* model payload — not the cleaned
 * {@link StructuredContact}. The model is allowed to emit `email`/`phone`
 * singular aliases, `organization`/`company` for `org`, and either a string or
 * an array for the contact-method fields; `cleanContact` is what normalizes all
 * of that down to the canonical StructuredContact shape. Validation here only
 * asserts the *envelope* is sane (an object with a `contacts` array of objects),
 * which is precisely the failure mode the repair loop exists to catch — a model
 * that returned prose, a bare array, or `{"contacts": "..."}`.
 */
const stringOrStringArray = z.union([z.string(), z.array(z.unknown())]).optional();

const RawContactSchema = z
  .object({
    name: z.unknown().optional(),
    emails: stringOrStringArray,
    email: stringOrStringArray,
    phones: stringOrStringArray,
    phone: stringOrStringArray,
    org: z.unknown().optional(),
    organization: z.unknown().optional(),
    company: z.unknown().optional(),
    title: z.unknown().optional(),
    notes: z.unknown().optional()
  })
  .loose();

const RawPayloadSchema = z.object({
  contacts: z.array(RawContactSchema)
});

type RawContact = z.infer<typeof RawContactSchema>;

/**
 * Reason codes for a structured extraction failure. `parse` = the model output
 * contained no JSON object / was not parseable JSON. `invalid-shape` = JSON
 * parsed but did not match the expected envelope (even after one repair).
 * `no-engine` = no on-device model is loaded.
 */
export type ExtractFailureReason = "parse" | "invalid-shape" | "no-engine";

/**
 * Structured result of {@link extractContactResult}. Never throws for the
 * normal model-output-was-garbage path — callers branch on `ok`.
 *
 * On failure, `canCloudRetry` is the SEAM for the paid "retry on the server"
 * upsell. THIS MODULE NEVER MAKES A NETWORK CALL — it is the on-device free
 * tier and must stay network-free. The flag (plus the optional `onCloudRetry`
 * callback accepted by the extract functions) lets a paid-aware caller decide
 * whether to offer/perform a server retry; the implementation of that retry
 * lives entirely in the caller.
 */
export type ExtractResult =
  | { ok: true; contacts: StructuredContact[]; contact: StructuredContact | null }
  | { ok: false; reason: ExtractFailureReason; rawText: string; canCloudRetry: boolean };

/**
 * Options for the extraction entry points. All optional — omitting them yields
 * the original behaviour.
 */
export type ExtractOptions = {
  /**
   * SEAM ONLY. Invoked (and awaited) on a terminal on-device failure so a
   * paid-tier caller can hand the raw text to the server extractor. This module
   * does not implement, wrap, or assume anything about the network call — it
   * merely forwards `rawText` and the failure `reason` and returns whatever the
   * caller's cloud path produces. Leaving this undefined keeps the module
   * strictly on-device / network-free.
   */
  onCloudRetry?: (rawText: string, reason: ExtractFailureReason) => Promise<StructuredContact[]>;
};

function arrayOfStrings(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

function cleanContact(raw: RawContact): StructuredContact | null {
  const name = String(raw.name ?? "").trim();
  const emails = arrayOfStrings(raw.emails ?? raw.email)
    .map((email) => email.toLowerCase())
    .filter((email) => !GENERIC_EMAIL_PREFIXES.some((prefix) => email.startsWith(prefix)));
  const phones = arrayOfStrings(raw.phones ?? raw.phone);
  const org = String(raw.org ?? raw.organization ?? raw.company ?? "").trim();
  const title = String(raw.title ?? "").trim();
  const notes = String(raw.notes ?? "").trim();

  if (!name && emails.length === 0 && phones.length === 0) return null;
  return {
    name: name || emails[0] || phones[0] || "Unknown contact",
    emails,
    phones,
    org: org || undefined,
    title: title || undefined,
    notes: notes || undefined
  };
}

/**
 * Outcome of trying to turn raw model text into validated contacts.
 *  - `valid`: text parsed AND matched the envelope schema. `contacts` is the
 *    cleaned list (possibly empty — an empty list is a *valid* answer, e.g. the
 *    "no real person, only a generic inbox" case).
 *  - `parse`: no JSON object found / `JSON.parse` threw.
 *  - `invalid-shape`: parsed JSON failed the zod envelope check.
 */
type ParseOutcome =
  | { kind: "valid"; contacts: StructuredContact[] }
  | { kind: "parse"; error: string }
  | { kind: "invalid-shape"; error: string };

function validateExtraction(text: string): ParseOutcome {
  const match = text.match(/\{[\s\S]*\}/);
  if (!match) return { kind: "parse", error: "No JSON object found in model output." };

  let parsedJson: unknown;
  try {
    parsedJson = JSON.parse(match[0]);
  } catch (err) {
    return { kind: "parse", error: err instanceof Error ? err.message : "JSON.parse failed." };
  }

  const result = RawPayloadSchema.safeParse(parsedJson);
  if (!result.success) {
    const issue = result.error.issues[0];
    const path = issue?.path?.length ? `${issue.path.join(".")}: ` : "";
    return { kind: "invalid-shape", error: `${path}${issue?.message ?? "did not match expected shape"}` };
  }

  const contacts = result.data.contacts
    .map(cleanContact)
    .filter((contact): contact is StructuredContact => Boolean(contact));
  return { kind: "valid", contacts };
}

/**
 * Backward-compatible best-effort parser: returns the cleaned contacts, or `[]`
 * on any parse/shape problem. Kept for callers that only want a list and do not
 * care about the failure reason. New code that needs the structured failure /
 * cloud-retry seam should use {@link extractContactResult}.
 */
export function parseExtractedContacts(text: string): StructuredContact[] {
  const outcome = validateExtraction(text);
  return outcome.kind === "valid" ? outcome.contacts : [];
}

function buildMessages(text: string): ChatMessage[] {
  return [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user", content: `Extract contacts from this text:\n\n${text.slice(0, 3000)}` }
  ];
}

/**
 * Core engine call + validate-and-repair loop. Returns a discriminated
 * {@link ParseOutcome} together with the last raw model text (for the failure
 * result / cloud-retry seam). Pure on-device: the only I/O is `engine.generate`.
 */
async function runExtraction(text: string): Promise<{ outcome: ParseOutcome; rawText: string }> {
  const engine = getActiveEngine();
  if (!engine) throw new Error("Load an on-device model before extracting contacts.");

  const messages = buildMessages(text);
  let rawText = await engine.generate(messages, { temperature: 0, maxTokens: 512 });
  let outcome = validateExtraction(rawText);
  if (outcome.kind === "valid") return { outcome, rawText };

  // ── Repair attempt (exactly once) ───────────────────────────────────────
  // Feed the prior (invalid) output and the concrete validation error back to
  // the model and ask for ONLY valid JSON matching the shape. Greedy decoding
  // is kept so the retry is deterministic.
  const repairMessages: ChatMessage[] = [
    ...messages,
    { role: "assistant", content: rawText },
    {
      role: "user",
      content: `Your previous output was invalid: ${outcome.error}. Return ONLY valid JSON matching this shape: ${SHAPE_HINT}.`
    }
  ];
  rawText = await engine.generate(repairMessages, { temperature: 0, maxTokens: 512 });
  outcome = validateExtraction(rawText);
  return { outcome, rawText };
}

/**
 * Run the on-device model over `text` and return a STRUCTURED result that
 * distinguishes success from failure without throwing for bad model output.
 *
 * Flow: generate → parse → zod-validate. On a parse failure or shape-invalid
 * output, the model is re-prompted ONCE with the validation error appended. If
 * it is still invalid, this returns `{ ok: false, reason, rawText, canCloudRetry }`
 * instead of throwing.
 *
 * Network-free by construction. If `opts.onCloudRetry` is supplied (the paid
 * "retry on the server" seam), it is awaited on terminal failure and, if it
 * yields contacts, the result is upgraded to `{ ok: true, ... }`; otherwise the
 * structured failure is returned with `canCloudRetry` reflecting whether a
 * server retry is even an option. The callback itself is the ONLY thing that
 * may touch the network, and it is owned entirely by the caller.
 *
 * The only thrown error is the pre-existing "no engine loaded" guard, surfaced
 * here as `{ ok: false, reason: "no-engine" }` so callers never get an uncaught
 * throw from the model path.
 */
export async function extractContactResult(text: string, opts?: ExtractOptions): Promise<ExtractResult> {
  let outcome: ParseOutcome;
  let rawText: string;
  try {
    ({ outcome, rawText } = await runExtraction(text));
  } catch {
    // No on-device engine is loaded. This is a structured failure too, so the
    // caller can offer the cloud-retry seam rather than crashing.
    const reason: ExtractFailureReason = "no-engine";
    return finalizeFailure(text, reason, opts);
  }

  if (outcome.kind === "valid") {
    return { ok: true, contacts: outcome.contacts, contact: outcome.contacts[0] ?? null };
  }

  return finalizeFailure(rawText, outcome.kind, opts);
}

/**
 * Build the failure result, optionally giving the caller-owned cloud-retry seam
 * a chance to recover. Stays network-free: the only escape hatch is the
 * caller-supplied `onCloudRetry`.
 */
async function finalizeFailure(
  rawText: string,
  reason: ExtractFailureReason,
  opts?: ExtractOptions
): Promise<ExtractResult> {
  if (opts?.onCloudRetry) {
    const recovered = await opts.onCloudRetry(rawText, reason);
    if (recovered.length > 0) {
      return { ok: true, contacts: recovered, contact: recovered[0] ?? null };
    }
  }
  return { ok: false, reason, rawText, canCloudRetry: true };
}

/**
 * Run the on-device model over `text` and return every structured contact it
 * found. Use this for pasted blurbs / multi-person cards that may describe more
 * than one person. Temperature is pinned to 0 (greedy) for stable JSON, matching
 * the Magic_Unicorn_Outreach extractor.
 *
 * Backward-compatible: still resolves to `StructuredContact[]` and still returns
 * `[]` when nothing usable is found. Now backed by the validate-and-repair loop,
 * so a first-pass JSON glitch gets one self-correction attempt before falling
 * back to `[]`. For the structured failure + cloud-retry seam use
 * {@link extractContactResult}.
 */
export async function extractContacts(text: string, opts?: ExtractOptions): Promise<StructuredContact[]> {
  const result = await extractContactResult(text, opts);
  return result.ok ? result.contacts : [];
}

/**
 * Extract a single structured contact from a business card or pasted text.
 * Returns the primary (first) contact, or `null` when nothing usable was found.
 * For multi-person input use {@link extractContacts}; for the structured
 * failure + cloud-retry seam use {@link extractContactResult}.
 *
 * Backward-compatible: signature still resolves to `StructuredContact | null`.
 */
export async function extractContact(text: string, opts?: ExtractOptions): Promise<StructuredContact | null> {
  const result = await extractContactResult(text, opts);
  return result.ok ? result.contact : null;
}
