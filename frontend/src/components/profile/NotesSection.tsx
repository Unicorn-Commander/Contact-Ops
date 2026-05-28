/**
 * NotesSection — a timestamped journal the user and AI agents share.
 *
 * Phase 1 surfaces this in Settings (target_type='user'); the same component
 * drops into person and org detail pages later by changing `targetType`
 * + `targetId` props.
 *
 * Composition:
 *   header     "Notes & Log" + short subtitle
 *   composer   autosize textarea + Cmd/Ctrl+Enter to submit
 *   list       newest-first entries with author chips, relative timestamps,
 *              optional source context, hover-only delete on user-authored
 *   loadmore   offset pagination, 20 at a time
 *
 * Empty / loading / error states are all first-class:
 *   - loading  three skeleton entries while the initial query is pending
 *   - error    single inline message with a Retry button
 *   - empty    friendly copy + small note icon
 *
 * The component is presentation-and-state only — the MCP tool wiring lives in
 * `useNotes.ts`. Test seam: the same component can be rendered with mock data
 * by the showcase preview (which substitutes its own minimal state instead of
 * the hooks).
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { formatDistanceToNowStrict } from "date-fns";
import { useAuth } from "react-oidc-context";
import { useReducedMotion } from "framer-motion";
import { Cpu, Loader2, MessageSquare, NotebookPen, Trash2, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, initials } from "@/lib/utils";
import { useAppendNote, useDeleteNote, useNotesList } from "@/hooks/useNotes";
import type { Note, NoteTargetType, UUID } from "@/lib/types";

// ---- Public props ---------------------------------------------------------

export interface NotesSectionProps {
  /** Defaults to "user" for the Settings surface. */
  targetType?: NoteTargetType;
  /** Required for person/org targets; omit / null for user. */
  targetId?: UUID | null;
  /** Optional override copy for re-use on person/org pages. */
  title?: string;
  subtitle?: string;
  /** Page size; defaults to 20 to match backend. */
  pageSize?: number;
  className?: string;
}

// ---- Source formatter -----------------------------------------------------
// The backend returns `source` as a JSON object (e.g. {proposal_id, cluster_id,
// action}); render it as a compact one-line label. Common conventions get
// pretty labels; anything else falls back to first-key:value.

function formatNoteSource(source: Record<string, unknown> | null | undefined): string | null {
  if (!source) return null;
  if (typeof source.proposal_id === "string") return `from proposal ${source.proposal_id.slice(0, 8)}`;
  if (typeof source.cluster_id === "string") return `from cluster ${source.cluster_id.slice(0, 8)}`;
  if (typeof source.action === "string") return String(source.action);
  const entries = Object.entries(source);
  if (entries.length === 0) return null;
  const [k, v] = entries[0];
  return `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`;
}

// ---- Author chips ---------------------------------------------------------
// Built inline (vs reusing AgentBadge / HumanBadge from design-system) so they
// can be slimmer for an inline-list context — same tokens, same intent.

function UserChip({ label }: { label: string }) {
  const fallback = initials(label || "You");
  return (
    <span
      className={cn(
        "inline-flex h-6 shrink-0 items-center gap-1.5 rounded-full border border-border bg-card px-2 text-[11px] font-medium leading-none text-foreground shadow-[var(--shadow-1)]"
      )}
      title={label ? `${label} (you)` : "You"}
      aria-label={label ? `${label}, you` : "You"}
    >
      <span
        aria-hidden="true"
        className={cn(
          "co-human-sigil inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-semibold"
        )}
      >
        {fallback || <UserRound className="h-3 w-3" strokeWidth={1.6} />}
      </span>
      <span className="truncate max-w-28">You</span>
    </span>
  );
}

function AgentChip({ slug }: { slug: string }) {
  // Slim AgentBadge equivalent. Hash → hue for the sigil tint so each agent
  // reads as a distinct color across the log.
  let hash = 2166136261;
  for (let i = 0; i < slug.length; i += 1) {
    hash ^= slug.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  const hue = Math.abs(hash % 360);
  const sigilStyle = {
    color: `oklch(0.78 0.18 ${hue})`,
    background: `oklch(0.68 0.15 ${hue} / 0.16)`
  } as const;
  return (
    <span
      className={cn(
        "inline-flex h-6 shrink-0 items-center gap-1.5 rounded-md border border-border bg-card px-2 font-mono text-[11px] font-medium leading-none text-foreground shadow-[var(--shadow-1)]"
      )}
      title={`Agent ${slug}`}
      aria-label={`Agent ${slug}`}
      data-agent-slug={slug}
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

// ---- Note body ------------------------------------------------------------
//
// We don't ship a markdown library yet — keep the dependency surface flat and
// render plain text with newlines preserved + a couple of safe in-line affordances:
//   - autolink http(s)://… spans (rel=noopener,noreferrer)
//   - inline `code` between backticks
// The shape gives agents room to format thoughts naturally without us having
// to vet a full markdown renderer for HIPAA-safe surfaces.

function renderInline(text: string): ReactNode[] {
  // First pass: split on backtick-delimited code.
  const parts: ReactNode[] = [];
  const codePattern = /`([^`]+)`/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let nodeIndex = 0;

  while ((match = codePattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(...linkify(text.slice(lastIndex, match.index), nodeIndex));
      nodeIndex += 1;
    }
    parts.push(
      <code
        key={`code-${nodeIndex++}`}
        className="rounded-sm bg-muted px-1 py-0.5 font-mono text-[12.5px] text-foreground"
      >
        {match[1]}
      </code>
    );
    lastIndex = codePattern.lastIndex;
  }
  if (lastIndex < text.length) {
    parts.push(...linkify(text.slice(lastIndex), nodeIndex));
  }
  return parts;
}

function linkify(text: string, baseIndex: number): ReactNode[] {
  const out: ReactNode[] = [];
  // Permissive URL regex; trailing punctuation is trimmed off the URL by hand
  // so "see https://example.com." doesn't include the period.
  const urlPattern = /(https?:\/\/[^\s]+)/g;
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  while ((m = urlPattern.exec(text)) !== null) {
    if (m.index > last) {
      out.push(text.slice(last, m.index));
    }
    let url = m[0];
    const trim = url.match(/([.,!?:;)\]]+)$/);
    let suffix = "";
    if (trim) {
      suffix = trim[1];
      url = url.slice(0, -suffix.length);
    }
    out.push(
      <a
        key={`url-${baseIndex}-${i++}`}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
      >
        {url}
      </a>
    );
    if (suffix) out.push(suffix);
    last = urlPattern.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function NoteBody({ text }: { text: string }) {
  // Preserve blank lines by splitting on \n\n into paragraphs, then within each
  // paragraph render inline (links + code) with single-newlines becoming <br>.
  const paragraphs = useMemo(() => text.replace(/\r\n/g, "\n").split(/\n{2,}/), [text]);
  return (
    <div className="space-y-2 text-sm leading-relaxed text-foreground">
      {paragraphs.map((paragraph, pIdx) => {
        const lines = paragraph.split("\n");
        return (
          <p key={pIdx} className="whitespace-pre-wrap break-words">
            {lines.map((line, lIdx) => (
              <span key={lIdx}>
                {renderInline(line)}
                {lIdx < lines.length - 1 ? <br /> : null}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

// ---- Entry row ------------------------------------------------------------

export interface NoteEntryProps {
  note: Note;
  /** True iff this user can delete it (author_type='user' && author_id === current uc_uid). */
  canDelete: boolean;
  onDelete?: (noteId: UUID) => void;
  /** Disable the delete button while a delete mutation is in flight. */
  deleting?: boolean;
  /** Pretty name to show in the user chip if we have it (Settings has the auth profile). */
  userDisplayName?: string;
}

function NoteEntry({ note, canDelete, onDelete, deleting, userDisplayName }: NoteEntryProps) {
  const relative = useMemo(() => {
    try {
      return formatDistanceToNowStrict(new Date(note.created_at), { addSuffix: true });
    } catch {
      return "just now";
    }
  }, [note.created_at]);

  const absolute = useMemo(() => {
    try {
      return new Date(note.created_at).toLocaleString(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit"
      });
    } catch {
      return note.created_at;
    }
  }, [note.created_at]);

  return (
    <div className="co-note-entry group relative rounded-lg border border-border bg-card/70 p-3 transition-colors hover:bg-muted/40 sm:p-4">
      <div className="flex flex-wrap items-center gap-2">
        {note.author_type === "agent" ? (
          <AgentChip slug={note.author_id || "agent"} />
        ) : (
          <UserChip label={userDisplayName ?? "You"} />
        )}
        <time
          dateTime={note.created_at}
          title={absolute}
          className="co-mono-numeric text-[11.5px] text-muted-foreground"
        >
          {relative}
        </time>
        {canDelete ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={cn(
              "ml-auto h-7 w-7 shrink-0 opacity-0 transition-opacity",
              "focus-visible:opacity-100 group-hover:opacity-100",
              deleting && "opacity-100"
            )}
            disabled={deleting}
            aria-label="Delete note"
            title="Delete note"
            onClick={() => onDelete?.(note.id)}
          >
            {deleting ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.6} />
            ) : (
              <Trash2 className="h-4 w-4 text-destructive" strokeWidth={1.6} />
            )}
          </Button>
        ) : null}
      </div>
      <div className="mt-2">
        <NoteBody text={note.text} />
      </div>
      {(() => {
        const sourceLabel = formatNoteSource(note.source);
        return sourceLabel ? (
          <p className="mt-1.5 text-[11.5px] text-muted-foreground">
            <span className="font-mono">{sourceLabel}</span>
          </p>
        ) : null;
      })()}
    </div>
  );
}

// ---- Empty / loading / error blocks --------------------------------------

function NotesEmpty() {
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

function NotesSkeleton() {
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

function NotesError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between"
    >
      <p className="text-destructive">Couldn't load notes: {message}</p>
      <Button variant="outline" size="sm" onClick={onRetry} className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive">
        Retry
      </Button>
    </div>
  );
}

// ---- Composer -------------------------------------------------------------

const COMPOSER_MAX_LINES = 8;

interface ComposerProps {
  onSubmit: (text: string) => void;
  busy: boolean;
  errorMessage?: string | null;
}

function NoteComposer({ onSubmit, busy, errorMessage }: ComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  // Autosize, same recipe as ChatInput.
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

  const submit = useCallback(() => {
    const text = value.trim();
    if (!text || busy) return;
    onSubmit(text);
    setValue("");
  }, [busy, onSubmit, value]);

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Cmd/Ctrl+Enter submits. Plain Enter inserts a newline (notes are
    // usually multi-line; we don't want to send half-finished thoughts).
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className="space-y-2"
    >
      <Textarea
        ref={textareaRef}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
        rows={2}
        aria-label="Write a note"
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
          {errorMessage ? (
            <span className="text-destructive"> · {errorMessage}</span>
          ) : null}
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

// ---- Top-level NotesSection ----------------------------------------------

export function NotesSection({
  targetType = "user",
  targetId = null,
  title = "Notes & Log",
  subtitle = "A journal you and your agents share.",
  pageSize = 20,
  className
}: NotesSectionProps) {
  const auth = useAuth();
  const profile = auth.user?.profile as Record<string, unknown> | undefined;
  const currentUserUcUid = profile?.uc_uid ? String(profile.uc_uid) : profile?.sub ? String(profile.sub) : "";
  const userDisplayName = String(profile?.name ?? profile?.preferred_username ?? profile?.email ?? "You");

  const list = useNotesList({ targetType, targetId, pageSize });
  const append = useAppendNote({ targetType, targetId });
  const remove = useDeleteNote({ targetType, targetId });

  const [deletingId, setDeletingId] = useState<UUID | null>(null);
  const [appendErr, setAppendErr] = useState<string | null>(null);

  // Clear inline composer error once a successful mutation lands.
  useEffect(() => {
    if (append.isSuccess) setAppendErr(null);
  }, [append.isSuccess]);

  const handleAppend = useCallback(
    (text: string) => {
      setAppendErr(null);
      append.mutate(
        { text },
        {
          onError: (error) => {
            setAppendErr(error instanceof Error ? error.message : "Couldn't post note. Try again.");
          }
        }
      );
    },
    [append]
  );

  const handleDelete = useCallback(
    (noteId: UUID) => {
      setDeletingId(noteId);
      remove.mutate(
        { noteId },
        {
          onSettled: () => setDeletingId(null)
        }
      );
    },
    [remove]
  );

  const prefersReducedMotion = useReducedMotion();

  const empty = !list.isLoading && !list.isError && list.notes.length === 0;

  return (
    <Card className={className} data-testid="notes-section">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <NotebookPen className="h-4 w-4 text-[oklch(var(--co-brand-500))]" strokeWidth={1.8} />
          {title}
        </CardTitle>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <NoteComposer onSubmit={handleAppend} busy={append.isPending} errorMessage={appendErr} />

        {list.isError ? (
          <NotesError
            message={list.error?.message ?? "Unknown error"}
            onRetry={() => void list.refetch()}
          />
        ) : null}

        {list.isLoading ? (
          <NotesSkeleton />
        ) : empty ? (
          <NotesEmpty />
        ) : (
          <div
            className={cn("space-y-3", !prefersReducedMotion && "co-animate-fade-in")}
            aria-live="polite"
          >
            {list.notes.map((note) => {
              const isAuthor =
                note.author_type === "user" && Boolean(currentUserUcUid) && note.author_id === currentUserUcUid;
              return (
                <NoteEntry
                  key={note.id}
                  note={note}
                  canDelete={isAuthor}
                  onDelete={handleDelete}
                  deleting={deletingId === note.id && remove.isPending}
                  userDisplayName={userDisplayName}
                />
              );
            })}
            {list.hasMore ? (
              <div className="flex justify-center pt-1">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={list.loadMore}
                  disabled={list.loadingMore}
                  aria-label="Load older notes"
                >
                  {list.loadingMore ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.8} />
                      Loading…
                    </>
                  ) : (
                    "Load more"
                  )}
                </Button>
              </div>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
