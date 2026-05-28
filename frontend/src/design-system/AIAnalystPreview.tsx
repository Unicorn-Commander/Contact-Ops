/**
 * AIAnalystPreview — auth-free showcase of the AI Contacts Analyst panel.
 *
 * Lives at /design-system so the panel is screenshottable without a Keycloak
 * session or a running backend. Renders each variant in an in-page "phone-like
 * frame" (a card with the right-aligned panel pinned inside) so the layout
 * matches what the user actually sees.
 *
 * Variants:
 *   - "closed"   shell with the Sparkles trigger button, panel collapsed
 *   - "empty"    panel open, empty-state with suggested prompts
 *   - "streaming" panel mid-stream: a user turn + an assistant turn currently
 *                 emitting `delta` tokens, plus a `find_person_by_identifier`
 *                 tool call resolving with entity chips
 *   - "long"     a long thread with several user/assistant exchanges + tool
 *                cards (including one error) so the scrollbar + density read
 *
 * Everything in this file is presentational. No fetch, no router calls, no
 * SSE — just a hand-rolled in-frame copy of the real panel.
 */
import { Bell, BookUser, Menu, Search, Sparkles, Plus, X, Wrench, User, Building2, AlertCircle, ArrowUp, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { KeyboardHint } from "@/design-system/KeyboardHint";
import { Spinner } from "@/design-system/Spinner";
import { cn } from "@/lib/utils";
import type { ThemeMode } from "@/design-system/tokens";

// ---- Shared local types (subset of useChat / ToolCall) ---------------------

type PreviewToolCall = {
  call_id: string;
  tool: string;
  arg_summary: string;
  state: "pending" | "ok" | "error";
  result_chips?: Array<{ kind: "person" | "org"; label: string }>;
  error?: string;
};

type PreviewMessage =
  | { role: "user"; content: string }
  | {
      role: "assistant";
      content: string;
      tool_calls?: PreviewToolCall[];
      streaming?: boolean;
      error?: string;
    };

// ---- Static demo data ------------------------------------------------------

const EMPTY_SUGGESTIONS = [
  { title: "Find someone by email", body: "Find the person whose primary email is REMOVED and summarize their last 5 interactions." },
  { title: "Founders with no email", body: "Show people tagged 'founder' who have no primary email on file." },
  { title: "What changed this week?", body: "Summarize the most important Contact-Ops changes in the last 7 days, grouped by tenant." },
  { title: "Stale relationships", body: "Which 10 people haven't had any interaction in 90+ days but are tagged 'priority'?" }
];

const STREAMING_THREAD: PreviewMessage[] = [
  {
    role: "user",
    content: "Find Alex Henderson and show me their last few interactions."
  },
  {
    role: "assistant",
    content: "Pulling Alex up now",
    streaming: true,
    tool_calls: [
      {
        call_id: "call_001",
        tool: "find_person_by_identifier",
        arg_summary: "identifier: REMOVED",
        state: "ok",
        result_chips: [{ kind: "person", label: "Alex Henderson" }]
      },
      {
        call_id: "call_002",
        tool: "list_action_events",
        arg_summary: "person_id: 6f3…, limit: 5",
        state: "pending"
      }
    ]
  }
];

const LONG_THREAD: PreviewMessage[] = [
  {
    role: "user",
    content: "Find me every founder at a YC company in the contacts."
  },
  {
    role: "assistant",
    content:
      "I'll cross-reference the 'founder' tag with organizations whose 'investor' field includes YC. One moment.",
    tool_calls: [
      {
        call_id: "call_010",
        tool: "search_people",
        arg_summary: "tags: ['founder'], limit: 25",
        state: "ok",
        result_chips: [
          { kind: "person", label: "Mina Chen" },
          { kind: "person", label: "Rafael Ortiz" },
          { kind: "person", label: "Priya Shah" }
        ]
      },
      {
        call_id: "call_011",
        tool: "search_organizations",
        arg_summary: "investors_includes: 'Y Combinator'",
        state: "ok",
        result_chips: [
          { kind: "org", label: "Bramble Robotics" },
          { kind: "org", label: "Northwind Labs" }
        ]
      }
    ]
  },
  {
    role: "assistant",
    content:
      "Three contacts match: Mina Chen (Bramble Robotics), Rafael Ortiz (Northwind Labs), and Priya Shah (also Bramble). I can open any of them, or filter further by stage or recency."
  },
  {
    role: "user",
    content: "Just the ones with no primary email."
  },
  {
    role: "assistant",
    content: "Filtering down to founders with no primary email on file…",
    tool_calls: [
      {
        call_id: "call_020",
        tool: "list_people",
        arg_summary: "tags: ['founder'], has_primary_email: false",
        state: "error",
        error: "MCP tool returned 503 — upstream temporarily unavailable. Try again."
      }
    ]
  },
  {
    role: "assistant",
    content:
      "One of the lookups failed; I'll retry the list and stitch the result back into the filter."
  }
];

// ---- Visual building blocks (lightweight twins of the real panel) ---------

function EntityChip({ chip }: { chip: { kind: "person" | "org"; label: string } }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-background/60 px-2 py-0.5 text-[11px] font-medium text-foreground">
      {chip.kind === "person" ? (
        <User className="h-3 w-3 text-[oklch(var(--co-sky-500))]" strokeWidth={1.8} />
      ) : (
        <Building2 className="h-3 w-3 text-[oklch(var(--co-emerald-500))]" strokeWidth={1.8} />
      )}
      {chip.label}
    </span>
  );
}

function ToolPreviewCard({ call }: { call: PreviewToolCall }) {
  const isError = call.state === "error";
  const resolved = call.state !== "pending";
  return (
    <div
      className={cn(
        "rounded-md border bg-background/60 px-2.5 py-2 text-[12px] shadow-[var(--shadow-1)]",
        isError
          ? "border-[oklch(var(--co-rose-500)/0.4)] bg-[oklch(var(--co-rose-500)/0.05)]"
          : resolved
            ? "border-border"
            : "border-dashed border-[oklch(var(--co-brand-500)/0.4)]"
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md",
            isError
              ? "bg-[oklch(var(--co-rose-500)/0.15)] text-[oklch(var(--co-rose-500))]"
              : "bg-[oklch(var(--co-brand-500)/0.15)] text-[oklch(var(--co-brand-500))]"
          )}
        >
          {isError ? (
            <AlertCircle className="h-3 w-3" strokeWidth={2} />
          ) : (
            <Wrench className="h-3 w-3" strokeWidth={2} />
          )}
        </span>
        <span className="font-mono text-[11.5px] font-medium text-foreground">{call.tool}</span>
        {!resolved ? (
          <span className="ml-auto inline-flex items-center gap-1 text-[10.5px] text-muted-foreground">
            <Spinner size="sm" label="" className="text-muted-foreground" />
            <span>running…</span>
          </span>
        ) : (
          <span className="ml-auto inline-flex items-center gap-0.5 text-[10.5px] font-medium text-muted-foreground">
            <ChevronRight className="h-3 w-3" strokeWidth={1.8} />
            Raw
          </span>
        )}
      </div>
      <p className="mt-1 truncate text-[11px] text-muted-foreground">{call.arg_summary}</p>
      {call.result_chips && call.result_chips.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {call.result_chips.map((chip, idx) => (
            <EntityChip key={`${chip.kind}-${idx}`} chip={chip} />
          ))}
        </div>
      ) : null}
      {isError ? (
        <p className="mt-1 text-[11px] text-[oklch(var(--co-rose-500))]">{call.error}</p>
      ) : null}
    </div>
  );
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary px-3 py-2 text-sm leading-snug text-primary-foreground shadow-[var(--shadow-1)]">
        {content}
      </div>
    </div>
  );
}

function AssistantBubble({
  content,
  tool_calls,
  streaming,
  cursorText
}: {
  content: string;
  tool_calls?: PreviewToolCall[];
  streaming?: boolean;
  cursorText?: string;
}) {
  const callList = tool_calls ?? [];
  return (
    <div className="flex justify-start">
      <div className="max-w-[92%] space-y-2 rounded-2xl rounded-tl-sm border border-border bg-card px-3 py-2.5 text-sm leading-snug text-card-foreground shadow-[var(--shadow-1)]">
        <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
          <Sparkles className="h-3 w-3 text-[oklch(var(--co-brand-500))]" strokeWidth={1.8} />
          AI Analyst
        </div>
        {content || cursorText ? (
          <p className="whitespace-pre-wrap break-words">
            {content}
            {cursorText ? <span className="text-muted-foreground">{cursorText}</span> : null}
            {streaming ? (
              <span
                className="co-streaming-cursor ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 rounded-full bg-[oklch(var(--primary))] align-middle"
                aria-hidden="true"
              />
            ) : null}
          </p>
        ) : null}
        {callList.length > 0 ? (
          <div className="space-y-1.5">
            {callList.map((call) => (
              <ToolPreviewCard key={call.call_id} call={call} />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Composer({ value = "", placeholder = "Ask about people, orgs, tags, or recent changes…" }: { value?: string; placeholder?: string }) {
  return (
    <div className="border-t border-border bg-card/80 p-3 backdrop-blur">
      <div className="relative">
        <div
          aria-hidden="true"
          className="min-h-[40px] w-full rounded-lg border border-input bg-background px-3 py-2 pr-10 text-sm leading-snug shadow-[var(--shadow-1)]"
        >
          {value ? (
            <span className="text-foreground">{value}</span>
          ) : (
            <span className="text-muted-foreground">{placeholder}</span>
          )}
        </div>
        <span className="bg-gradient-brand absolute bottom-1 right-1 flex h-7 w-7 items-center justify-center rounded-md text-primary-foreground shadow-[var(--shadow-1)]">
          <ArrowUp className="h-3.5 w-3.5" strokeWidth={2} />
        </span>
      </div>
      <p className="mt-1 text-[10.5px] leading-tight text-muted-foreground">
        <span className="font-mono">Enter</span> to send · <span className="font-mono">Shift+Enter</span> newline ·{" "}
        <span className="font-mono">Esc</span> closes
      </p>
    </div>
  );
}

function PanelHeader({ statusText }: { statusText: string }) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-3">
      <span className="bg-gradient-brand flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-primary-foreground shadow-[var(--shadow-1)]">
        <Sparkles className="h-4 w-4" strokeWidth={1.8} />
      </span>
      <div className="min-w-0 flex-1 leading-tight">
        <p className="truncate text-sm font-semibold">AI Contacts Analyst</p>
        <p className="truncate text-[11px] text-muted-foreground">{statusText}</p>
      </div>
      <span
        aria-hidden="true"
        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground"
      >
        <Plus className="h-4 w-4" strokeWidth={1.8} />
      </span>
      <span
        aria-hidden="true"
        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground"
      >
        <X className="h-4 w-4" strokeWidth={1.8} />
      </span>
    </header>
  );
}

function EmptyStateBody({ onSelect }: { onSelect?: (prompt: string) => void }) {
  return (
    <div className="mx-auto flex max-w-sm flex-col items-center gap-4 px-2 pt-6 text-center">
      <span className="bg-gradient-brand co-glow-brand flex h-12 w-12 items-center justify-center rounded-2xl text-primary-foreground">
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
          {EMPTY_SUGGESTIONS.map((prompt) => (
            <li key={prompt.title}>
              <button
                type="button"
                onClick={() => onSelect?.(prompt.body)}
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
        Press <KeyboardHint keys="mod+J" label="Toggle AI Analyst" className="mx-0.5" /> any time to toggle this panel.
      </p>
    </div>
  );
}

// ---- The phone-like frame --------------------------------------------------

type Variant = "closed" | "empty" | "streaming" | "long";

function MockTopBar({ withSparkles }: { withSparkles: boolean }) {
  return (
    <header className="co-glass sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border px-3">
      <span className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground lg:hidden">
        <Menu className="h-4 w-4" strokeWidth={1.8} />
      </span>
      <div className="flex items-center gap-2 rounded-md border border-border bg-background/60 px-2 py-1 text-[11px] text-muted-foreground">
        <BookUser className="h-3.5 w-3.5 text-[oklch(var(--co-brand-500))]" strokeWidth={1.8} />
        Magic Unicorn
      </div>
      <button
        type="button"
        className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 text-[11px] text-muted-foreground"
      >
        <Search className="h-3.5 w-3.5" strokeWidth={1.8} />
        Search…
      </button>
      {withSparkles ? (
        <span
          aria-hidden="true"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground"
          title="AI Contacts Analyst"
        >
          <span className="bg-gradient-brand flex h-5 w-5 items-center justify-center rounded-md text-primary-foreground shadow-[var(--shadow-1)]">
            <Sparkles className="h-3 w-3" strokeWidth={2} />
          </span>
        </span>
      ) : null}
      <span aria-hidden="true" className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground">
        <Bell className="h-4 w-4" strokeWidth={1.8} />
      </span>
    </header>
  );
}

function MockPageBody({ compact }: { compact?: boolean }) {
  // A handful of placeholder cards so the underlying app is visible behind the panel.
  return (
    <div className={cn("space-y-3 p-4", compact && "p-3")}>
      <h1 className="text-base font-semibold">Dashboard</h1>
      <div className={cn("grid gap-3", compact ? "grid-cols-2" : "grid-cols-3")}>
        {Array.from({ length: compact ? 4 : 6 }).map((_, i) => (
          <div key={i} className="rounded-md border border-border bg-card p-3 shadow-[var(--shadow-1)]">
            <p className="text-[10.5px] font-medium uppercase tracking-[0.1em] text-muted-foreground">Stat</p>
            <p className="mt-1 text-lg font-semibold">1,2{(i + 3) * 7}</p>
            <p className="mt-0.5 text-[10.5px] text-muted-foreground">last 7d</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function Frame({
  theme,
  variant,
  width = "100%",
  compact = false
}: {
  theme: ThemeMode;
  variant: Variant;
  width?: string | number;
  compact?: boolean;
}) {
  const isMobile = typeof width === "number" && width <= 420;
  const isOpen = variant !== "closed";

  // Mid-stream "thinking" cursor — animate three dots between renders without
  // actual SSE. Pure aesthetics; respects reduced-motion via tokens.css.
  const [cursor, setCursor] = useState(".");
  useEffect(() => {
    if (variant !== "streaming") return;
    const id = window.setInterval(() => {
      setCursor((value) => (value.length >= 3 ? "." : value + "."));
    }, 450);
    return () => window.clearInterval(id);
  }, [variant]);

  return (
    <div
      data-theme={theme}
      style={{ width }}
      className={cn(
        "relative isolate overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background text-foreground shadow-[var(--shadow-2)]"
      )}
    >
      <div className="flex">
        {!isMobile ? (
          <aside className="hidden w-44 shrink-0 border-r border-border bg-[oklch(var(--sidebar))] lg:block">
            <div className="flex h-14 items-center gap-2 border-b border-border px-3">
              <span className="bg-gradient-brand flex h-7 w-7 items-center justify-center rounded-lg text-primary-foreground">
                <BookUser className="h-3.5 w-3.5" strokeWidth={1.8} />
              </span>
              <span className="text-xs font-semibold">Contact-Ops</span>
            </div>
            <nav className="space-y-1 p-2">
              {["Dashboard", "People", "Organizations", "Tags", "Review Queue"].map((item, i) => (
                <span
                  key={item}
                  className={cn(
                    "flex h-8 items-center gap-2 rounded-md border-l-2 px-2 text-[11px] font-medium",
                    i === 0
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-transparent text-muted-foreground"
                  )}
                >
                  {item}
                </span>
              ))}
            </nav>
          </aside>
        ) : null}
        <div className="min-w-0 flex-1">
          <MockTopBar withSparkles />
          <MockPageBody compact={compact} />
        </div>
      </div>

      {/* Mobile-only backdrop dim — only when the panel is open on small frames. */}
      {isMobile && isOpen ? (
        <div className="absolute inset-0 z-40 bg-background/55 backdrop-blur-sm" aria-hidden="true" />
      ) : null}

      {/* The right-rail panel. On the mobile frame it covers the full width. */}
      {isOpen ? (
        <div
          className={cn(
            "absolute inset-y-0 right-0 z-50 flex flex-col border-l border-border bg-background shadow-[var(--shadow-4)]",
            isMobile ? "w-full" : "w-[360px]"
          )}
        >
          <PanelHeader
            statusText={
              variant === "empty"
                ? "Ask anything about people, orgs, or tags."
                : variant === "streaming"
                  ? "Streaming response"
                  : "Conversation ready"
            }
          />
          <div className="co-scrollbar flex-1 overflow-y-auto px-3 py-4">
            {variant === "empty" ? (
              <EmptyStateBody />
            ) : (
              <div className="flex flex-col gap-3">
                {(variant === "streaming" ? STREAMING_THREAD : LONG_THREAD).map((message, idx) => {
                  if (message.role === "user") {
                    return <UserBubble key={idx} content={message.content} />;
                  }
                  const isLast = idx === (variant === "streaming" ? STREAMING_THREAD.length : LONG_THREAD.length) - 1;
                  return (
                    <AssistantBubble
                      key={idx}
                      content={message.content}
                      tool_calls={message.tool_calls}
                      streaming={Boolean(message.streaming) && isLast}
                      cursorText={
                        variant === "streaming" && isLast && message.streaming ? cursor : undefined
                      }
                    />
                  );
                })}
              </div>
            )}
          </div>
          <Composer
            value={variant === "empty" ? "" : variant === "streaming" ? "" : "Just the ones with no primary email."}
          />
        </div>
      ) : null}
    </div>
  );
}

// ---- Public showcase entry -------------------------------------------------

export function AIAnalystPreview() {
  const previews = useMemo(
    () => [
      { theme: "dark" as ThemeMode, variant: "closed" as Variant, title: "Closed · Sparkles trigger in top bar (dark)" },
      { theme: "light" as ThemeMode, variant: "closed" as Variant, title: "Closed · Sparkles trigger in top bar (light)" },
      { theme: "dark" as ThemeMode, variant: "empty" as Variant, title: "Open · empty state with suggested prompts (dark)" },
      { theme: "light" as ThemeMode, variant: "empty" as Variant, title: "Open · empty state with suggested prompts (light)" },
      { theme: "dark" as ThemeMode, variant: "streaming" as Variant, title: "Mid-stream · tool call + streaming delta (dark)" },
      { theme: "light" as ThemeMode, variant: "streaming" as Variant, title: "Mid-stream · tool call + streaming delta (light)" },
      { theme: "dark" as ThemeMode, variant: "long" as Variant, title: "Long thread · multi-turn with tool error (dark)" },
      { theme: "light" as ThemeMode, variant: "long" as Variant, title: "Long thread · multi-turn with tool error (light)" }
    ],
    []
  );

  // Use a single shared ref so the parent layout can be tuned (just placeholder).
  const containerRef = useRef<HTMLDivElement | null>(null);

  return (
    <section ref={containerRef} className="space-y-6">
      <div>
        <h2 className="text-base font-semibold">AI Contacts Analyst</h2>
        <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
          The right-rail chat panel. Trigger from the top-bar Sparkles button or the global ⌘J hotkey. Streams
          assistant tokens, inlines MCP tool calls with entity chips, and lives at the same elevation as the
          command palette.
        </p>
      </div>

      {/* Pair desktop+light frames in a two-up grid; mobile gets its own row. */}
      <div className="grid gap-6 2xl:grid-cols-2">
        {previews.map(({ theme, variant, title }) => (
          <div key={`${theme}-${variant}`} className="space-y-2">
            <h3 className="text-sm font-medium">{title}</h3>
            <Frame theme={theme} variant={variant} />
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Mobile (375px) · panel open empty (dark)</h3>
        <div className="mx-auto w-[375px] max-w-full">
          <Frame theme="dark" variant="empty" width={375} compact />
        </div>
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Mobile (375px) · panel mid-stream (light)</h3>
        <div className="mx-auto w-[375px] max-w-full">
          <Frame theme="light" variant="streaming" width={375} compact />
        </div>
      </div>
    </section>
  );
}

export default AIAnalystPreview;
