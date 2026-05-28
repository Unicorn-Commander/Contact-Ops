/**
 * NotesPreview — auth-free showcase of the Settings Notes & Log card.
 *
 * Renders the same composition the real `NotesSection` ships with, but on a
 * mocked in-memory store (no auth, no MCP). Lives at /design-system/notes so
 * the screenshot harness can capture every state.
 *
 * Variants:
 *   - empty       composer + the "no notes yet" friendly empty state
 *   - populated   mix of user + agent entries, one entry has a source context
 *   - loading     three skeleton entries
 *   - error       inline destructive message with a Retry button
 *   - focused     composer focused with a draft note
 *   - mobile      375px frame width with two entries (used to validate the
 *                 mobile screenshot acceptance criterion)
 *
 * Both light + dark themes are rendered for the desktop variants. The mobile
 * frame ships dark since most acceptance gates are dark.
 */
import { Cpu, Loader2, MessageSquare, NotebookPen, Trash2, UserRound } from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { formatDistanceToNowStrict, subDays, subHours, subMinutes } from "date-fns";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, initials } from "@/lib/utils";
import type { ThemeMode } from "@/design-system/tokens";

// ---- Mock note shape (matches lib/types Note) -----------------------------

type PreviewNote = {
  id: string;
  author_type: "user" | "agent";
  author_id: string;
  author_display_name?: string;
  text: string;
  source?: string | null;
  created_at: string;
};

const NOW = new Date();
const MOCK_USER_DISPLAY = "Aaron Stransky";
const MOCK_USER_UCUID = "aaron";

const POPULATED_NOTES: PreviewNote[] = [
  {
    id: "n1",
    author_type: "agent",
    author_id: "ai-contacts-analyst",
    text: "Confirmed primary email for Rocky Doe from the latest proposal — REMOVED (last verified by SES bounce check).\n\nNo conflicting addresses on file.",
    source: "from proposal #4f2a91",
    created_at: subMinutes(NOW, 8).toISOString()
  },
  {
    id: "n2",
    author_type: "user",
    author_id: MOCK_USER_UCUID,
    author_display_name: MOCK_USER_DISPLAY,
    text: "Set my preferred pronouns to **they/them** in the Keycloak profile and confirmed it propagates to CardDAV exports.",
    created_at: subHours(NOW, 2).toISOString()
  },
  {
    id: "n3",
    author_type: "agent",
    author_id: "dedup-agent",
    text: "Found 3 dedup candidates against the Sullivan case file. Routed to the Review Queue at `tier=2` — see `https://contacts.example/review?cluster=8aa1`.",
    source: "from inbox cluster 8aa1",
    created_at: subHours(NOW, 7).toISOString()
  },
  {
    id: "n4",
    author_type: "user",
    author_id: MOCK_USER_UCUID,
    author_display_name: MOCK_USER_DISPLAY,
    text: "Heads up: I'm muting auto-apply for the next 24h while the agents settle in. Will turn it back on tomorrow.",
    created_at: subDays(NOW, 1).toISOString()
  }
];

// ---- Inline chips (slimmed copies of NotesSection's internals) ------------

function UserChipMock({ label }: { label: string }) {
  const fallback = initials(label || "You");
  return (
    <span
      className={cn(
        "inline-flex h-6 shrink-0 items-center gap-1.5 rounded-full border border-border bg-card px-2 text-[11px] font-medium leading-none text-foreground shadow-[var(--shadow-1)]"
      )}
      title={label}
      aria-label={`Human ${label}`}
    >
      <span
        aria-hidden="true"
        className="co-human-sigil inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-semibold"
      >
        {fallback || <UserRound className="h-3 w-3" strokeWidth={1.6} />}
      </span>
      <span className="truncate max-w-28">You</span>
    </span>
  );
}

function AgentChipMock({ slug }: { slug: string }) {
  let hash = 2166136261;
  for (let i = 0; i < slug.length; i += 1) {
    hash ^= slug.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  const hue = Math.abs(hash % 360);
  const sigilStyle: CSSProperties = {
    color: `oklch(0.78 0.18 ${hue})`,
    background: `oklch(0.68 0.15 ${hue} / 0.16)`
  };
  return (
    <span
      className={cn(
        "inline-flex h-6 shrink-0 items-center gap-1.5 rounded-md border border-border bg-card px-2 font-mono text-[11px] font-medium leading-none text-foreground shadow-[var(--shadow-1)]"
      )}
      title={`Agent ${slug}`}
      aria-label={`Agent ${slug}`}
    >
      <span
        aria-hidden="true"
        className="inline-flex h-4 w-4 items-center justify-center rounded-md"
        style={sigilStyle}
      >
        <Cpu className="h-2.5 w-2.5" strokeWidth={1.8} />
      </span>
      <span className="truncate max-w-36">AI · {slug}</span>
    </span>
  );
}

// ---- Note body (light markdown-ish: links + inline code + bold) -----------

function renderInlineMock(text: string): (string | JSX.Element)[] {
  // Order: pull out `code` first, then **bold**, then http(s) links.
  // Keeps the preview faithful to NotesSection's behavior without copying
  // the full implementation verbatim.
  const out: (string | JSX.Element)[] = [];
  const codePattern = /`([^`]+)`|\*\*([^*]+)\*\*|(https?:\/\/[^\s]+)/g;
  let lastIndex = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = codePattern.exec(text)) !== null) {
    if (m.index > lastIndex) out.push(text.slice(lastIndex, m.index));
    if (m[1] !== undefined) {
      out.push(
        <code key={`c-${i++}`} className="rounded-sm bg-muted px-1 py-0.5 font-mono text-[12.5px] text-foreground">
          {m[1]}
        </code>
      );
    } else if (m[2] !== undefined) {
      out.push(
        <strong key={`b-${i++}`} className="font-semibold text-foreground">
          {m[2]}
        </strong>
      );
    } else if (m[3] !== undefined) {
      let url = m[3];
      const trim = url.match(/([.,!?:;)\]]+)$/);
      let suffix = "";
      if (trim) {
        suffix = trim[1];
        url = url.slice(0, -suffix.length);
      }
      out.push(
        <a
          key={`l-${i++}`}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
        >
          {url}
        </a>
      );
      if (suffix) out.push(suffix);
    }
    lastIndex = codePattern.lastIndex;
  }
  if (lastIndex < text.length) out.push(text.slice(lastIndex));
  return out;
}

function NoteBodyMock({ text }: { text: string }) {
  const paragraphs = useMemo(() => text.replace(/\r\n/g, "\n").split(/\n{2,}/), [text]);
  return (
    <div className="space-y-2 text-sm leading-relaxed text-foreground">
      {paragraphs.map((paragraph, pIdx) => {
        const lines = paragraph.split("\n");
        return (
          <p key={pIdx} className="whitespace-pre-wrap break-words">
            {lines.map((line, lIdx) => (
              <span key={lIdx}>
                {renderInlineMock(line)}
                {lIdx < lines.length - 1 ? <br /> : null}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

// ---- Entry row (mocked) ---------------------------------------------------

function NoteEntryMock({
  note,
  showDelete,
  currentUserName
}: {
  note: PreviewNote;
  showDelete: boolean;
  currentUserName: string;
}) {
  const relative = useMemo(() => {
    try {
      return formatDistanceToNowStrict(new Date(note.created_at), { addSuffix: true });
    } catch {
      return "just now";
    }
  }, [note.created_at]);
  const absolute = useMemo(() => new Date(note.created_at).toLocaleString(), [note.created_at]);

  return (
    <div className="co-note-entry group relative rounded-lg border border-border bg-card/70 p-3 transition-colors hover:bg-muted/40 sm:p-4">
      <div className="flex flex-wrap items-center gap-2">
        {note.author_type === "agent" ? (
          <AgentChipMock slug={note.author_id} />
        ) : (
          <UserChipMock label={note.author_display_name ?? currentUserName} />
        )}
        <time
          dateTime={note.created_at}
          title={absolute}
          className="co-mono-numeric text-[11.5px] text-muted-foreground"
        >
          {relative}
        </time>
        {showDelete ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="ml-auto h-7 w-7 shrink-0 opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
            aria-label="Delete note"
          >
            <Trash2 className="h-4 w-4 text-destructive" strokeWidth={1.6} />
          </Button>
        ) : null}
      </div>
      <div className="mt-2">
        <NoteBodyMock text={note.text} />
      </div>
      {note.source ? (
        <p className="mt-1.5 text-[11.5px] text-muted-foreground">
          <span className="font-mono">{note.source}</span>
        </p>
      ) : null}
    </div>
  );
}

// ---- States --------------------------------------------------------------

function EmptyMock() {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border bg-muted/20 px-4 py-10 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
        <MessageSquare className="h-5 w-5" strokeWidth={1.6} />
      </span>
      <p className="text-sm font-medium text-foreground">No notes yet.</p>
      <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">
        Add one above, or the AI Analyst will start logging here as it works.
      </p>
    </div>
  );
}

function SkeletonMock() {
  return (
    <div className="space-y-3" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <div key={i} className="rounded-lg border border-border bg-card/40 p-3 sm:p-4">
          <div className="flex items-center gap-2">
            <Skeleton className="h-6 w-20 rounded-full" />
            <Skeleton className="h-3 w-16" />
          </div>
          <div className="mt-3 space-y-2">
            <Skeleton className="h-3.5 w-[88%]" />
            <Skeleton className="h-3.5 w-[72%]" />
            <Skeleton className="h-3.5 w-[55%]" />
          </div>
        </div>
      ))}
    </div>
  );
}

function ErrorMock() {
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between"
    >
      <p className="text-destructive">Couldn't load notes: MCP request failed with HTTP 502</p>
      <Button
        variant="outline"
        size="sm"
        className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
      >
        Retry
      </Button>
    </div>
  );
}

// ---- Composer (mock — local-state textarea, no submit) -------------------

const COMPOSER_MAX_LINES = 8;

function ComposerMock({
  initialValue = "",
  autoFocus = false,
  busy = false
}: {
  initialValue?: string;
  autoFocus?: boolean;
  busy?: boolean;
}) {
  const [value, setValue] = useState(initialValue);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    const lineHeight = parseFloat(window.getComputedStyle(node).lineHeight || "20");
    const maxHeight = lineHeight * COMPOSER_MAX_LINES + 16;
    const next = Math.min(node.scrollHeight, maxHeight);
    node.style.height = `${next}px`;
    node.style.overflowY = node.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [value]);

  return (
    <form className="space-y-2" onSubmit={(event) => event.preventDefault()}>
      <Textarea
        ref={textareaRef}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        rows={2}
        aria-label="Write a note"
        autoFocus={autoFocus}
        placeholder="Add a note. Cmd/Ctrl + Enter to post."
        className={cn(
          "min-h-[72px] resize-none rounded-lg leading-relaxed",
          "shadow-[var(--shadow-1)] transition-shadow focus:shadow-[var(--shadow-2)]"
        )}
        disabled={busy}
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] leading-tight text-muted-foreground">
          <span className="font-mono">Cmd/Ctrl + Enter</span> to post
        </p>
        <Button type="submit" disabled={!value.trim() || busy} className="min-w-[110px]">
          {busy ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.8} />
              Posting…
            </>
          ) : (
            <>Post note</>
          )}
        </Button>
      </div>
    </form>
  );
}

// ---- The composed card (mocked) ------------------------------------------

type Variant = "empty" | "populated" | "loading" | "error" | "focused";

function NotesCardMock({
  variant,
  notes = POPULATED_NOTES,
  currentUserName = MOCK_USER_DISPLAY
}: {
  variant: Variant;
  notes?: PreviewNote[];
  currentUserName?: string;
}) {
  return (
    <Card data-testid={`notes-preview-${variant}`}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <NotebookPen className="h-4 w-4 text-[oklch(var(--co-brand-500))]" strokeWidth={1.8} />
          Notes & Log
        </CardTitle>
        <CardDescription>A journal you and your agents share.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <ComposerMock
          initialValue={variant === "focused" ? "Following up on Rocky — pull the dedup cluster after lunch." : ""}
          autoFocus={variant === "focused"}
        />
        {variant === "error" ? <ErrorMock /> : null}
        {variant === "loading" ? (
          <SkeletonMock />
        ) : variant === "empty" ? (
          <EmptyMock />
        ) : variant === "error" ? null : (
          <div className="space-y-3">
            {notes.map((note) => (
              <NoteEntryMock
                key={note.id}
                note={note}
                currentUserName={currentUserName}
                showDelete={note.author_type === "user" && note.author_id === MOCK_USER_UCUID}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---- Theme frame (matches AIAnalystPreview's framing) --------------------

function Frame({
  theme,
  variant,
  width = "100%",
  mobile = false
}: {
  theme: ThemeMode;
  variant: Variant;
  width?: string | number;
  mobile?: boolean;
}) {
  return (
    <div
      data-theme={theme}
      style={{ width }}
      className={cn(
        "co-glass rounded-[var(--radius-lg)] border border-border bg-background p-4 text-foreground shadow-[var(--shadow-2)]",
        mobile ? "p-3" : "p-4 sm:p-6"
      )}
    >
      <NotesCardMock variant={variant} />
    </div>
  );
}

// ---- Public entry --------------------------------------------------------

export function NotesPreview() {
  const desktopPairs: Array<{ theme: ThemeMode; variant: Variant; title: string }> = [
    { theme: "dark", variant: "empty", title: "Empty state · no notes yet (dark)" },
    { theme: "light", variant: "empty", title: "Empty state · no notes yet (light)" },
    { theme: "dark", variant: "populated", title: "Populated · mix of user + agent entries (dark)" },
    { theme: "light", variant: "populated", title: "Populated · mix of user + agent entries (light)" },
    { theme: "dark", variant: "focused", title: "Composer focused · draft note (dark)" },
    { theme: "light", variant: "focused", title: "Composer focused · draft note (light)" },
    { theme: "dark", variant: "loading", title: "Loading · skeleton entries (dark)" },
    { theme: "light", variant: "error", title: "Error · inline retry (light)" }
  ];

  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-base font-semibold">Notes & Log</h2>
        <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
          The shared journal between you and your agents. Lives on Settings (target_type='user') first; the same
          component drops into person + org detail pages later. Each entry shows who logged it, when, the body
          (with light markdown — bold, inline code, autolinks), and an optional source line. Hover on a
          user-authored entry to reveal the delete affordance.
        </p>
      </div>

      <div className="grid gap-6 2xl:grid-cols-2">
        {desktopPairs.map(({ theme, variant, title }) => (
          <div key={`${theme}-${variant}`} className="space-y-2">
            <h3 className="text-sm font-medium">{title}</h3>
            <Frame theme={theme} variant={variant} />
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Mobile (375px) · populated (dark)</h3>
        <div className="mx-auto w-[375px] max-w-full">
          <Frame theme="dark" variant="populated" width={375} mobile />
        </div>
      </div>
    </section>
  );
}

export default NotesPreview;
