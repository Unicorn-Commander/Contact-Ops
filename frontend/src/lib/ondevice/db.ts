import { openDB, type DBSchema, type IDBPDatabase } from "idb";

export type StructuredContact = {
  name: string;
  emails: string[];
  phones: string[];
  org?: string;
  title?: string;
  notes?: string;
};

export type LocalContact = StructuredContact & {
  id: string;
  createdAt: string;
  updatedAt: string;
  source: "manual" | "extraction" | "import";
  embedding?: Float32Array;
};

export type EmbeddingCacheEntry = {
  id: string;
  text: string;
  embedding: Float32Array;
  createdAt: string;
};

export type LocalConversation = {
  id: string;
  title: string;
  messages: Array<{ role: "user" | "assistant" | "system"; content: string; createdAt: number }>;
  createdAt: string;
  updatedAt: string;
};

interface ContactOpsLocalDB extends DBSchema {
  contacts: {
    key: string;
    value: LocalContact;
    indexes: {
      "by-name": string;
      "by-email": string;
      "by-org": string;
      "by-created": string;
    };
  };
  embeddingCache: {
    key: string;
    value: EmbeddingCacheEntry;
    indexes: { "by-text": string };
  };
  conversations: {
    key: string;
    value: LocalConversation;
    indexes: { "by-updated": string };
  };
}

const DB_NAME = "contact-ops-ondevice";
const DB_VERSION = 1;

function uuid(prefix: string): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

function primaryEmail(contact: StructuredContact): string {
  return contact.emails[0]?.toLowerCase() ?? "";
}

export function contactToEmbeddingText(contact: StructuredContact): string {
  return [
    contact.name,
    contact.title,
    contact.org,
    contact.emails.join(" "),
    contact.phones.join(" "),
    contact.notes
  ]
    .filter(Boolean)
    .join("\n");
}

class OndeviceDB {
  private db: IDBPDatabase<ContactOpsLocalDB> | null = null;

  async init(): Promise<IDBPDatabase<ContactOpsLocalDB>> {
    if (this.db) return this.db;
    this.db = await openDB<ContactOpsLocalDB>(DB_NAME, DB_VERSION, {
      upgrade(db) {
        const contacts = db.createObjectStore("contacts", { keyPath: "id" });
        contacts.createIndex("by-name", "name");
        contacts.createIndex("by-email", "emails.0");
        contacts.createIndex("by-org", "org");
        contacts.createIndex("by-created", "createdAt");

        const embeddingCache = db.createObjectStore("embeddingCache", { keyPath: "id" });
        embeddingCache.createIndex("by-text", "text", { unique: true });

        const conversations = db.createObjectStore("conversations", { keyPath: "id" });
        conversations.createIndex("by-updated", "updatedAt");
      }
    });
    return this.db;
  }

  async addContact(contact: StructuredContact, opts?: { source?: LocalContact["source"]; embedding?: Float32Array }): Promise<LocalContact> {
    const db = await this.init();
    const now = new Date().toISOString();
    const saved: LocalContact = {
      ...contact,
      emails: contact.emails.map((email) => email.trim().toLowerCase()).filter(Boolean),
      phones: contact.phones.map((phone) => phone.trim()).filter(Boolean),
      id: uuid("contact"),
      createdAt: now,
      updatedAt: now,
      source: opts?.source ?? "manual",
      embedding: opts?.embedding
    };
    await db.put("contacts", saved);
    return saved;
  }

  async updateContact(id: string, patch: Partial<LocalContact>): Promise<LocalContact | undefined> {
    const db = await this.init();
    const existing = await db.get("contacts", id);
    if (!existing) return undefined;
    const updated = { ...existing, ...patch, id, updatedAt: new Date().toISOString() };
    await db.put("contacts", updated);
    return updated;
  }

  async deleteContact(id: string): Promise<void> {
    const db = await this.init();
    await db.delete("contacts", id);
  }

  async getAllContacts(): Promise<LocalContact[]> {
    const db = await this.init();
    return db.getAll("contacts");
  }

  async getContact(id: string): Promise<LocalContact | undefined> {
    const db = await this.init();
    return db.get("contacts", id);
  }

  async findByEmail(email: string): Promise<LocalContact | undefined> {
    const contacts = await this.getAllContacts();
    const normalized = email.trim().toLowerCase();
    return contacts.find((contact) => contact.emails.some((candidate) => candidate.toLowerCase() === normalized));
  }

  async getEmbedding(text: string): Promise<Float32Array | undefined> {
    const db = await this.init();
    return (await db.getFromIndex("embeddingCache", "by-text", text))?.embedding;
  }

  async setEmbedding(text: string, embedding: Float32Array): Promise<void> {
    const db = await this.init();
    await db.put("embeddingCache", {
      id: uuid("embedding"),
      text,
      embedding,
      createdAt: new Date().toISOString()
    });
  }

  async saveConversation(conversation: Omit<LocalConversation, "createdAt" | "updatedAt"> & Partial<Pick<LocalConversation, "createdAt" | "updatedAt">>): Promise<LocalConversation> {
    const db = await this.init();
    const now = new Date().toISOString();
    const saved: LocalConversation = {
      ...conversation,
      createdAt: conversation.createdAt ?? now,
      updatedAt: now
    };
    await db.put("conversations", saved);
    return saved;
  }

  async getConversations(): Promise<LocalConversation[]> {
    const db = await this.init();
    const conversations = await db.getAll("conversations");
    return conversations.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }

  async exportData(): Promise<string> {
    const db = await this.init();
    const serializeContact = (contact: LocalContact) => ({
      ...contact,
      embedding: contact.embedding ? Array.from(contact.embedding) : undefined
    });
    const serializeEmbedding = (entry: EmbeddingCacheEntry) => ({
      ...entry,
      embedding: Array.from(entry.embedding)
    });
    return JSON.stringify(
      {
        contacts: (await db.getAll("contacts")).map(serializeContact),
        embeddingCache: (await db.getAll("embeddingCache")).map(serializeEmbedding),
        conversations: await db.getAll("conversations"),
        exportedAt: new Date().toISOString()
      },
      null,
      2
    );
  }

  async importData(jsonData: string): Promise<{ contacts: number; conversations: number }> {
    const db = await this.init();
    const data = JSON.parse(jsonData) as {
      contacts?: Array<LocalContact & { embedding?: number[] }>;
      embeddingCache?: Array<EmbeddingCacheEntry & { embedding: number[] }>;
      conversations?: LocalConversation[];
    };
    let contacts = 0;
    let conversations = 0;

    for (const contact of data.contacts ?? []) {
      await db.put("contacts", {
        ...contact,
        emails: contact.emails ?? (primaryEmail(contact) ? [primaryEmail(contact)] : []),
        phones: contact.phones ?? [],
        embedding: contact.embedding ? new Float32Array(contact.embedding) : undefined
      });
      contacts += 1;
    }
    for (const entry of data.embeddingCache ?? []) {
      await db.put("embeddingCache", {
        ...entry,
        embedding: new Float32Array(entry.embedding)
      });
    }
    for (const conversation of data.conversations ?? []) {
      await db.put("conversations", conversation);
      conversations += 1;
    }
    return { contacts, conversations };
  }

  async clearAll(): Promise<void> {
    const db = await this.init();
    await Promise.all([db.clear("contacts"), db.clear("embeddingCache"), db.clear("conversations")]);
  }
}

export const ondeviceDB = new OndeviceDB();
