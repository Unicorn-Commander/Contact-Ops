/**
 * OndeviceAnalyst — "Ask the Analyst (on-device)" right-rail.
 * ===========================================================
 * A fully-local sibling of AIAnalystPanel. It mirrors that panel's look (same
 * Radix Dialog right-rail, same header lockup, same MessageBubble-style turns,
 * same composer affordances) but it NEVER touches the network:
 *
 *   - the model runs in-browser via useOndeviceLLM (WebGPU / transformers.js)
 *   - "context" for a question is gathered by semantic search over the LOCAL
 *     IndexedDB contact store (useOndeviceSearch) and stuffed into the prompt
 *   - there is NO /chat, NO /api/* call, NO auth token — everything is on-device
 *
 * This is intentionally a separate component from AIAnalystPanel (which streams
 * SSE from the backend over `useChat`). We do not reuse `useChat` here because
 * that hook is hard-wired to a backend SSE transport; the on-device path owns a
 * tiny local message-state machine instead and streams tokens straight from the
 * WebGPU engine's `onDelta` callback.
 *
 * Until a model is loaded, the thread area shows the <ModelLoader /> so the user
 * can pick + download a model without leaving the panel.
 */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { AlertCircle, Plus, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { ChatInput } from "@/components/chat/ChatInput";
import { ModelLoader } from "@/components/ondevice/ModelLoader";
import { cn } from "@/lib/utils";
import { useOndeviceLLM } from "@/hooks/useOndeviceLLM";
import { useOndeviceSearch, type SearchResult } from "@/hooks/useOndeviceSearch";
import type { ChatMessage as LLMChatMessage } from "@/lib/ondevice/llm";
import type { LocalContact } from "@/lib/ondevice/db";

export interface OndeviceAnalystProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// ---- Local thread types ---------------------------------------------------

type LocalRole = "user" | "assistant";

type LocalMessage = {
  id: string;
  role: LocalRole;
  content: string;
  /** True while the assistant turn is still streaming tokens. */
  streaming?: boolean;
  /** Set if this turn ended in an error. */
  error?: string;
  /** Contacts pulled into the prompt for this turn (assistant turns only). */
  context?: LocalContact[];
  createdAt: number;
};

// ---- Suggested empty-state prompts (local-flavored) -----------------------

const SUGGESTED_PROMPTS = [
  {
    title: "Summarize a person",
    body: "Summarize what we know about Alex, including any gaps in their contact info."
  },
  {
    title: "Who works where?",
    body: "Which of my contacts work at Acme, and what are their roles?"
  },
  {
    title: "Draft an intro",
    body: "Draft a short, warm introduction email between two of my contacts in the same industry."
  },
  {
    title: "Find by topic",
    body: "Who do I know that's connected to fundraising or investors?"
  }
] as const;

// ---- How many local contacts to stuff into the prompt ---------------------

const CONTEXT_LIMIT = 6;
/** Below this similarity we don't bother including a contact as context. */
const CONTEXT_MIN_SCORE = 0.2;

const SYSTEM_PROMPT =
  "You are the Contact-Ops on-device analyst. You answer questions about the " +
  "user's personal contacts using ONLY the contact records provided as context. " +
  "Everything runs locally on the user's device; never claim to look anything up " +
  "online. If the context does not contain the answer, say so plainly and note " +
  "what's missing. Be concise and concrete.";

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `local_${Math.random().toString(36).slice(2)}_${Date.now()}`;
}

/** Render a single local contact as a compact, model-friendly block. */
function contactToContextBlock(contact: LocalContact, index: number): string {
  const lines = [
    `[Contact ${index + 1}] ${contact.name || "(no name)"}`,
    contact.title ? `Title: ${contact.title}` : null,
    contact.org ? `Organization: ${contact.org}` : null,
    contact.emails.length ? `Emails: ${contact.emails.join(", ")}` : "Emails: (none on file)",
    contact.phones.length ? `Phones: ${contact.phones.join(", ")}` : null,
    contact.notes ? `Notes: ${contact.notes}` : null
  ].filter(Boolean);
  return lines.join("\n");
}

/** Build the local LLM message list: system + context + the conversation. */
function buildPrompt(history: LocalMessage[], question: string, context: LocalContact[]): LLMChatMessage[] {
  const contextText = context.length
    ? context.map(contactToContextBlock).join("\n\n")
    : "(no matching contacts were found in the local store)";

  const priorTurns: LLMChatMessage[] = history
    .filter((message) => !message.error)
    .map((message) => ({ role: message.role, content: message.content }));

  return [
    { role: "system", content: SYSTEM_PROMPT },
    {
      role: "system",
      content: `Relevant contacts from the local store:\n\n${contextText}`
    },
    ...priorTurns,
    { role: "user", content: question }
  ];
}

// ---- Auto-scroll (mirrors AIAnalystPanel) ---------------------------------

function useAutoScroll(deps: ReadonlyArray<unknown>) {
  const prefersReduced = useReducedMotion();
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    if (distance > 96) return;
    node.scrollTo({ top: node.scrollHeight, behavior: prefersReduced ? "auto" : "smooth" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return ref;
}

// ---- Main panel -----------------------------------------------------------

export function OndeviceAnalyst({ open, onOpenChange }: OndeviceAnalystProps) {
  const { status, load, chat } = useOndeviceLLM();
  const { search } = useOndeviceSearch();

  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const modelReady = status.status === "ready";
  const isEmpty = messages.length === 0;
  const scrollRef = useAutoScroll([messages, busy]);

  // Cancel any in-flight generation on unmount.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (!last || !last.streaming) return prev;
      return [...prev.slice(0, -1), { ...last, streaming: false }];
    });
  }, []);

  const handleNewChat = useCallback(() => {
    cancel();
    setMessages([]);
    setDraft("");
  }, [cancel]);

  const handleSelectPrompt = useCallback((prompt: string) => {
    setDraft(prompt);
  }, []);

  const handleSend = useCallback(
    (content: string) => {
      const question = content.trim();
      if (!question || busy || !modelReady) return;
      setDraft("");

      const userMessage: LocalMessage = {
        id: newId(),
        role: "user",
        content: question,
        createdAt: Date.now()
      };
      const assistantId = newId();
      const history = messages;

      setMessages((prev) => [
        ...prev,
        userMessage,
        { id: assistantId, role: "assistant", content: "", streaming: true, createdAt: Date.now() }
      ]);
      setBusy(true);

      const controller = new AbortController();
      abortRef.current = controller;

      void (async () => {
        try {
          // 1) Gather local context via semantic search over IndexedDB.
          let context: LocalContact[] = [];
          try {
            const hits: SearchResult[] = await search(question, CONTEXT_LIMIT);
            context = hits
              .filter((hit) => hit.score >= CONTEXT_MIN_SCORE)
              .map((hit): LocalContact => {
                const { score, ...contact } = hit;
                void score;
                return contact;
              });
          } catch {
            // Embeddings unavailable / empty store — proceed with no context
            // rather than failing the whole turn.
            context = [];
          }
          if (controller.signal.aborted) return;

          setMessages((prev) =>
            prev.map((message) => (message.id === assistantId ? { ...message, context } : message))
          );

          // 2) Stream the answer straight from the on-device engine.
          const prompt = buildPrompt(history, question, context);
          await chat(
            prompt,
            (delta) => {
              if (controller.signal.aborted) return;
              setMessages((prev) =>
                prev.map((message) =>
                  message.id === assistantId ? { ...message, content: message.content + delta } : message
                )
              );
            },
            { signal: controller.signal }
          );

          if (controller.signal.aborted) return;
          setMessages((prev) =>
            prev.map((message) => (message.id === assistantId ? { ...message, streaming: false } : message))
          );
        } catch (caught) {
          if (controller.signal.aborted) return;
          const message = caught instanceof Error ? caught.message : "On-device generation failed.";
          setMessages((prev) =>
            prev.map((entry) =>
              entry.id === assistantId ? { ...entry, streaming: false, error: message } : entry
            )
          );
        } finally {
          if (abortRef.current === controller) abortRef.current = null;
          setBusy(false);
        }
      })();
    },
    [busy, chat, messages, modelReady, search]
  );

  const subtitle = busy
    ? "Thinking on-device…"
    : !modelReady
      ? "Load a local model to begin."
      : isEmpty
        ? "Private. Runs entirely on this device."
        : "On-device conversation ready";

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="co-animate-fade-in fixed inset-0 z-40 bg-background/60 backdrop-blur-sm md:bg-transparent md:backdrop-blur-0" />
        <DialogPrimitive.Content
          aria-label="On-device Contacts Analyst"
          className={cn(
            "co-slide-rail fixed inset-y-0 right-0 z-50 flex h-full w-full flex-col border-l border-border bg-background text-foreground shadow-[var(--shadow-4)]",
            "md:w-[420px]"
          )}
          onOpenAutoFocus={(event) => event.preventDefault()}
        >
          {/* Header — mirrors AIAnalystPanel, with an "on-device" badge. */}
          <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-3">
            <span
              className="bg-gradient-brand flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-primary-foreground shadow-[var(--shadow-1)]"
              aria-hidden="true"
            >
              <Sparkles className="h-4 w-4" strokeWidth={1.8} />
            </span>
            <div className="min-w-0 flex-1 leading-tight">
              <DialogPrimitive.Title className="flex items-center gap-1.5 truncate text-sm font-semibold">
                Contacts Analyst
                <span className="inline-flex items-center rounded-full bg-[oklch(var(--co-emerald-500)/0.12)] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.06em] text-[oklch(var(--co-emerald-500))]">
                  On-device
                </span>
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="truncate text-[11px] text-muted-foreground">
                {subtitle}
              </DialogPrimitive.Description>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={handleNewChat}
              disabled={isEmpty && !busy}
              aria-label="Start a new on-device chat"
              title="New chat"
            >
              <Plus className="h-4 w-4" strokeWidth={1.8} />
            </Button>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Close on-device Analyst">
                <X className="h-4 w-4" strokeWidth={1.8} />
              </Button>
            </DialogPrimitive.Close>
          </header>

          {/* Thread */}
          <div
            ref={scrollRef}
            className="co-scrollbar flex-1 overflow-y-auto px-3 py-4"
            role="log"
            aria-live="polite"
            aria-relevant="additions text"
            aria-atomic="false"
          >
            {!modelReady ? (
              <div className="space-y-4">
                <ModelLoader status={status} onLoad={load} compact />
                <p className="px-1 text-[11px] leading-relaxed text-muted-foreground">
                  The model and your contacts stay on this device. Questions are answered locally with no
                  network calls.
                </p>
              </div>
            ) : isEmpty ? (
              <EmptyState onSelect={handleSelectPrompt} />
            ) : (
              <ul className="flex flex-col gap-3">
                {messages.map((message) => (
                  <LocalBubble key={message.id} message={message} />
                ))}
              </ul>
            )}
          </div>

          {/* Composer — only enabled once a model is ready. */}
          {modelReady ? (
            <ChatInput
              onSend={handleSend}
              onCancel={cancel}
              busy={busy}
              initialValue={draft}
              autoFocus
              placeholder="Ask about your contacts — answered on-device…"
            />
          ) : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

// ---- Local message bubble (mirrors chat/MessageBubble visuals) ------------

function LocalBubble({ message }: { message: LocalMessage }) {
  if (message.role === "user") {
    return (
      <li className="flex justify-end" aria-label="You said">
        <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary px-3 py-2 text-sm leading-snug text-primary-foreground shadow-[var(--shadow-1)]">
          {message.content}
        </div>
      </li>
    );
  }

  const hasContent = message.content.length > 0;
  const showCursor = Boolean(message.streaming);
  const contextCount = message.context?.length ?? 0;

  return (
    <li className="flex justify-start" aria-label="On-device Analyst said">
      <div
        className={cn(
          "max-w-[92%] space-y-2 rounded-2xl rounded-tl-sm border border-border bg-card px-3 py-2.5 text-sm leading-snug text-card-foreground shadow-[var(--shadow-1)]",
          message.error && "border-[oklch(var(--co-rose-500)/0.5)] bg-[oklch(var(--co-rose-500)/0.06)]"
        )}
      >
        <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
          <Sparkles className="h-3 w-3 text-[oklch(var(--co-brand-500))]" strokeWidth={1.8} />
          On-device Analyst
        </div>

        {hasContent ? (
          <p className="whitespace-pre-wrap break-words">
            {message.content}
            {showCursor ? (
              <span
                className="co-streaming-cursor ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 rounded-full bg-[oklch(var(--primary))] align-middle"
                aria-hidden="true"
              />
            ) : null}
          </p>
        ) : null}

        {showCursor && !hasContent ? (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="co-animate-pulse-subtle inline-block h-1.5 w-1.5 rounded-full bg-[oklch(var(--primary))]" />
            Thinking…
          </p>
        ) : null}

        {message.error ? (
          <p className="flex items-start gap-1.5 text-xs text-[oklch(var(--co-rose-500))]">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.8} />
            <span>{message.error}</span>
          </p>
        ) : null}

        {!message.error && contextCount > 0 ? (
          <p className="text-[10.5px] text-muted-foreground">
            Grounded in {contextCount} local contact{contextCount === 1 ? "" : "s"}.
          </p>
        ) : null}
      </div>
    </li>
  );
}

// ---- Empty state (mirrors AIAnalystPanel) ---------------------------------

function EmptyState({ onSelect }: { onSelect: (prompt: string) => void }) {
  return (
    <div className="mx-auto flex max-w-sm flex-col items-center gap-4 px-2 pt-6 text-center">
      <span
        className="bg-gradient-brand co-glow-brand flex h-12 w-12 items-center justify-center rounded-2xl text-primary-foreground"
        aria-hidden="true"
      >
        <Sparkles className="h-6 w-6" strokeWidth={1.6} />
      </span>
      <div className="space-y-1.5">
        <h2 className="bg-gradient-brand-text text-lg font-semibold tracking-tight">On-device Analyst</h2>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Ask in plain English. Answers are generated locally on your device and grounded in the contacts
          stored in this browser — nothing is sent over the network.
        </p>
      </div>
      <div className="w-full space-y-1.5">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          Try one of these
        </p>
        <ul className="flex flex-col gap-1.5">
          {SUGGESTED_PROMPTS.map((prompt) => (
            <li key={prompt.title}>
              <button
                type="button"
                onClick={() => onSelect(prompt.body)}
                className="focus-ring w-full rounded-md border border-border bg-card px-3 py-2 text-left transition-colors hover:border-[oklch(var(--primary)/0.5)] hover:bg-muted"
              >
                <p className="text-xs font-medium text-foreground">{prompt.title}</p>
                <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">{prompt.body}</p>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
