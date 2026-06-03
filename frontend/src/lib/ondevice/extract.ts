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

type ContactPayload = {
  contacts?: Array<{
    name?: unknown;
    emails?: unknown;
    email?: unknown;
    phones?: unknown;
    phone?: unknown;
    org?: unknown;
    organization?: unknown;
    company?: unknown;
    title?: unknown;
    notes?: unknown;
  }>;
};

function arrayOfStrings(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

function cleanContact(raw: NonNullable<ContactPayload["contacts"]>[number]): StructuredContact | null {
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

export function parseExtractedContacts(text: string): StructuredContact[] {
  const match = text.match(/\{[\s\S]*\}/);
  if (!match) return [];
  try {
    const parsed = JSON.parse(match[0]) as ContactPayload;
    if (!Array.isArray(parsed.contacts)) return [];
    return parsed.contacts.map(cleanContact).filter((contact): contact is StructuredContact => Boolean(contact));
  } catch {
    return [];
  }
}

/**
 * Run the on-device model over `text` and return every structured contact it
 * found. Use this for pasted blurbs / multi-person cards that may describe more
 * than one person. Temperature is pinned to 0 (greedy) for stable JSON, matching
 * the Magic_Unicorn_Outreach extractor.
 */
export async function extractContacts(text: string): Promise<StructuredContact[]> {
  const engine = getActiveEngine();
  if (!engine) throw new Error("Load an on-device model before extracting contacts.");

  const messages: ChatMessage[] = [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user", content: `Extract contacts from this text:\n\n${text.slice(0, 3000)}` }
  ];
  const output = await engine.generate(messages, { temperature: 0, maxTokens: 512 });
  return parseExtractedContacts(output);
}

/**
 * Extract a single structured contact from a business card or pasted text.
 * Returns the primary (first) contact, or `null` when nothing usable was found.
 * For multi-person input use {@link extractContacts}.
 */
export async function extractContact(text: string): Promise<StructuredContact | null> {
  const [first] = await extractContacts(text);
  return first ?? null;
}
