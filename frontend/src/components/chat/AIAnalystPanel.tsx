/**
 * AIAnalystPanel — the right-rail "AI Contacts Analyst".
 *
 * A controlled slide-over (Radix Dialog) that lives at the right edge:
 *   - desktop  → fixed 420px right-aligned column with a slim backdrop
 *   - mobile   → full-width sheet, darker backdrop
 *
 * Composition:
 *   header   "AI Contacts Analyst" + reset + close
 *   thread   scrollable list of <MessageBubble />, with empty-state suggestions
 *   composer sticky <ChatInput /> at the bottom
 *
 * Streaming:
 *   - the panel owns `useChat` and passes a real SSE transport built on top of
 *     `sseStream(...)` in lib/mcp.ts.
 *   - the design-system preview (AIAnalystPreview) injects a mocked async
 *     iterator so the showcase renders all states without a backend.
 *
 * Focus & a11y:
 *   - Radix traps focus while open; Esc closes (default Radix behavior).
 *   - the composer auto-focuses on open so the user can start typing.
 *   - thread auto-scrolls to bottom on new content; respects
 *     prefers-reduced-motion (jump instead of smooth scroll).
 *
 * Keyboard:
 *   - ⌘J open/close — bound in AppShell (matches the ⌘K pattern there).
 */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Sparkles, Plus, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "react-oidc-context";
import { useReducedMotion } from "framer-motion";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { KeyboardHint } from "@/design-system/KeyboardHint";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ChatInput } from "@/components/chat/ChatInput";
import { sseStream, type SSEEvent } from "@/lib/mcp";
import { useChat, type ChatMessage, type OpenChatStream } from "@/hooks/useChat";

export interface AIAnalystPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Override the chat transport — used by the design-system preview to inject
   * a mocked SSE generator. Defaults to the real `/chat` SSE endpoint, using
   * the access token from react-oidc-context.
   */
  openStream?: OpenChatStream;
  /** Optional seed messages (the preview uses this to land mid-thread). */
  initialMessages?: ChatMessage[];
  /** Suppress the auth read — used by the auth-free showcase preview. */
  disableAuth?: boolean;
}

// ---- Suggested empty-state prompts ----------------------------------------

const SUGGESTED_PROMPTS = [
  {
    title: "Find someone by email",
    body: "Find the person whose primary email is REMOVED and summarize their last 5 interactions."
  },
  {
    title: "Founders with no email",
    body: "Show people tagged 'founder' who have no primary email on file."
  },
  {
    title: "What changed this week?",
    body: "Summarize the most important Contact-Ops changes in the last 7 days, grouped by tenant."
  },
  {
    title: "Stale relationships",
    body: "Which 10 people haven't had any interaction in 90+ days but are tagged 'priority'?"
  }
] as const;

// ---- Auto-scroll hook -----------------------------------------------------

function useAutoScroll(deps: ReadonlyArray<unknown>) {
  const prefersReduced = useReducedMotion();
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    // If the user has scrolled up (more than ~96px from the bottom), don't yank
    // them back — let them read history while the stream continues.
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    if (distance > 96) return;
    node.scrollTo({
      top: node.scrollHeight,
      behavior: prefersReduced ? "auto" : "smooth"
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return ref;
}

// ---- Helpers --------------------------------------------------------------

/** Wrap the SSE transport with the current bearer token. */
function makeAuthedStream(token: string): OpenChatStream {
  return (payload, signal) =>
    (async function* () {
      const generator = sseStream("/chat", payload, token, { signal });
      for await (const event of generator) {
        yield event as SSEEvent;
      }
    })();
}

// ---- Main panel -----------------------------------------------------------

export function AIAnalystPanel({
  open,
  onOpenChange,
  openStream,
  initialMessages,
  disableAuth = false
}: AIAnalystPanelProps) {
  // Pull the access token without violating the rules-of-hooks when the
  // preview opts out of auth via disableAuth.
  const auth = useAuth();
  const token = disableAuth ? "" : auth.user?.access_token ?? "";

  // The default real transport closes over the current token at the time the
  // stream opens (re-derived per render is fine — sending re-reads it).
  const transport = useMemo<OpenChatStream>(() => {
    if (openStream) return openStream;
    return makeAuthedStream(token);
  }, [openStream, token]);

  const chat = useChat({ openStream: transport, initialMessages });
  const { messages, status, error, send, reset } = chat;

  const isBusy = status === "sending" || status === "streaming";
  const isEmpty = messages.length === 0;

  // The text the composer should pre-fill (suggested-prompt clicks).
  const [draft, setDraft] = useState("");

  const scrollRef = useAutoScroll([messages, status]);

  const handleSelectPrompt = useCallback((prompt: string) => {
    setDraft(prompt);
  }, []);

  const handleSend = useCallback(
    (content: string) => {
      send(content);
      setDraft("");
    },
    [send]
  );

  const handleNewChat = useCallback(() => {
    reset();
    setDraft("");
  }, [reset]);

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        {/* Mobile-only backdrop — desktop keeps the app interactive. */}
        <DialogPrimitive.Overlay
          className="co-animate-fade-in fixed inset-0 z-40 bg-background/60 backdrop-blur-sm md:bg-transparent md:backdrop-blur-0"
        />
        <DialogPrimitive.Content
          aria-label="AI Contacts Analyst"
          className={cn(
            "co-slide-rail fixed inset-y-0 right-0 z-50 flex h-full w-full flex-col border-l border-border bg-background text-foreground shadow-[var(--shadow-4)]",
            "md:w-[420px]"
          )}
          onOpenAutoFocus={(event) => {
            // Let the composer take focus, not the close button.
            event.preventDefault();
          }}
        >
          {/* Header */}
          <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-3">
            <span
              className="bg-gradient-brand flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-primary-foreground shadow-[var(--shadow-1)]"
              aria-hidden="true"
            >
              <Sparkles className="h-4 w-4" strokeWidth={1.8} />
            </span>
            <div className="min-w-0 flex-1 leading-tight">
              <DialogPrimitive.Title className="truncate text-sm font-semibold">
                AI Contacts Analyst
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="truncate text-[11px] text-muted-foreground">
                {isBusy
                  ? status === "sending"
                    ? "Reaching out…"
                    : "Streaming response"
                  : isEmpty
                    ? "Ask anything about people, orgs, or tags."
                    : "Conversation ready"}
              </DialogPrimitive.Description>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={handleNewChat}
              disabled={isEmpty && status === "idle"}
              aria-label="Start a new chat"
              title="New chat"
            >
              <Plus className="h-4 w-4" strokeWidth={1.8} />
            </Button>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Close AI Analyst">
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
            {isEmpty ? (
              <EmptyState onSelect={handleSelectPrompt} />
            ) : (
              <ul className="flex flex-col gap-3">
                {messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
                {status === "sending" && messages[messages.length - 1]?.role === "user" ? (
                  <li className="flex justify-start" aria-label="AI Analyst is thinking">
                    <div className="inline-flex items-center gap-1.5 rounded-2xl rounded-tl-sm border border-border bg-card px-3 py-2 text-xs text-muted-foreground shadow-[var(--shadow-1)]">
                      <span className="co-animate-pulse-subtle inline-block h-1.5 w-1.5 rounded-full bg-[oklch(var(--primary))]" />
                      Thinking…
                    </div>
                  </li>
                ) : null}
                {error ? (
                  <li className="rounded-md border border-[oklch(var(--co-rose-500)/0.4)] bg-[oklch(var(--co-rose-500)/0.06)] px-3 py-2 text-xs text-[oklch(var(--co-rose-500))]">
                    {error}
                  </li>
                ) : null}
              </ul>
            )}
          </div>

          {/* Composer */}
          <ChatInput
            onSend={handleSend}
            onCancel={chat.cancel}
            busy={isBusy}
            initialValue={draft}
            autoFocus
          />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

// ---- Empty state ----------------------------------------------------------

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
        <h2 className="bg-gradient-brand-text text-lg font-semibold tracking-tight">AI Contacts Analyst</h2>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Ask in plain English. The analyst has read-only access to your tenant's people, organizations, tags,
          and recent action events.
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
      <p className="mt-1 text-[10.5px] text-muted-foreground">
        Press <KeyboardHint keys="mod+J" label="Toggle AI Analyst" className="mx-0.5" /> any time to toggle this
        panel.
      </p>
    </div>
  );
}
