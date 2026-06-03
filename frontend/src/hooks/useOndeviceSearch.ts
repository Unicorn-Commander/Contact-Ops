import { useCallback, useState } from "react";
import { cosine, embed } from "@/lib/ondevice/embeddings";
import {
  contactToEmbeddingText,
  ondeviceDB,
  type LocalContact,
  type StructuredContact
} from "@/lib/ondevice/db";

export type SearchResult = LocalContact & { score: number };

/** cosine >= this counts as a duplicate (per on-device build spec). */
const DUPLICATE_THRESHOLD = 0.9;

export type DuplicatePair = {
  a: LocalContact;
  b: LocalContact;
  score: number;
  reason: string;
};

async function embedAndCache(text: string): Promise<Float32Array> {
  const cached = await ondeviceDB.getEmbedding(text);
  if (cached) return cached;
  const [embedding] = await embed([text]);
  await ondeviceDB.setEmbedding(text, embedding);
  return embedding;
}

async function ensureContactEmbedding(contact: LocalContact): Promise<Float32Array> {
  if (contact.embedding) return contact.embedding;
  const embedding = await embedAndCache(contactToEmbeddingText(contact));
  await ondeviceDB.updateContact(contact.id, { embedding });
  return embedding;
}

/**
 * Persist a contact and compute+cache its embedding in the same step, so search
 * and dedup never have to lazily backfill it later. Safe to call from any save
 * flow (manual add, extraction confirm, import). Runs entirely on-device.
 */
export async function saveContactWithEmbedding(
  contact: StructuredContact,
  opts?: { source?: LocalContact["source"] }
): Promise<LocalContact> {
  const embedding = await embedAndCache(contactToEmbeddingText(contact));
  return ondeviceDB.addContact(contact, { source: opts?.source, embedding });
}

function sharesEmail(a: LocalContact, b: LocalContact): boolean {
  const emails = new Set(a.emails.map((email) => email.toLowerCase()));
  return b.emails.some((email) => emails.has(email.toLowerCase()));
}

function normalizedName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export function useOndeviceSearch() {
  const [loading, setLoading] = useState(false);

  const search = useCallback(async (query: string, limit = 10): Promise<SearchResult[]> => {
    const text = query.trim();
    if (!text) return [];
    setLoading(true);
    try {
      const [queryEmbedding] = await embed([text]);
      const contacts = await ondeviceDB.getAllContacts();
      const scored = await Promise.all(
        contacts.map(async (contact) => ({
          ...contact,
          score: cosine(queryEmbedding, await ensureContactEmbedding(contact))
        }))
      );
      return scored
        .sort((a, b) => b.score - a.score)
        .slice(0, limit);
    } finally {
      setLoading(false);
    }
  }, []);

  const findDuplicates = useCallback(async (): Promise<DuplicatePair[]> => {
    setLoading(true);
    try {
      const contacts = await ondeviceDB.getAllContacts();
      const pairs: DuplicatePair[] = [];
      for (let i = 0; i < contacts.length; i += 1) {
        for (let j = i + 1; j < contacts.length; j += 1) {
          const a = contacts[i];
          const b = contacts[j];
          const sameEmail = sharesEmail(a, b);
          const sameName = normalizedName(a.name) !== "" && normalizedName(a.name) === normalizedName(b.name);
          let score = sameEmail || sameName ? 1 : 0;
          if (score === 0) score = cosine(await ensureContactEmbedding(a), await ensureContactEmbedding(b));
          if (score > DUPLICATE_THRESHOLD || sameEmail || sameName) {
            pairs.push({
              a,
              b,
              score,
              reason: sameEmail ? "Shared email" : sameName ? "Same normalized name" : "High semantic similarity"
            });
          }
        }
      }
      return pairs.sort((a, b) => b.score - a.score);
    } finally {
      setLoading(false);
    }
  }, []);

  const saveContact = useCallback(
    (contact: StructuredContact, opts?: { source?: LocalContact["source"] }) =>
      saveContactWithEmbedding(contact, opts),
    []
  );

  return { loading, search, findDuplicates, saveContact };
}
