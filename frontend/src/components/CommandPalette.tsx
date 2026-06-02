/**
 * Global command palette (⌘K).
 *
 * The dual-mode "quick lookup": press ⌘K (or focus the top-bar search) → type
 * a name → jump straight to a person / organization / tag. Built on `cmdk`
 * wrapped in a Radix dialog overlay so it sits above the whole app.
 *
 *  - Empty query  → Recent (localStorage) + Favorites + Quick actions.
 *  - Typed query  → debounced MCP search across People / Organizations / Tags,
 *                   plus any Quick actions whose label matches.
 *
 * Filtering is server-side (MCP `search_people` / `search_organizations`, with
 * a local filter for the small Tags set), so `shouldFilter={false}` — we render
 * exactly what we put in the list and let cmdk own arrow / Enter / Escape
 * navigation. This is the recommended pattern for async/remote palettes.
 *
 * This is a NEW, self-contained component. The inbox keeps its own specialised
 * verb palette at `components/inbox/CommandPalette.tsx` (untouched).
 */
import { Command } from "cmdk";
import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Building2,
  ListChecks,
  Plus,
  Search,
  Tags as TagsIcon,
  Upload,
  UserPlus,
  Users
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Dialog, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Spinner } from "@/design-system/Spinner";
import { useAccessToken, useQueryKeys } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import { cn } from "@/lib/utils";

/** A destination the palette can jump to. */
export type PaletteTarget =
  | { kind: "person"; id: string; label: string; sublabel?: string }
  | { kind: "org"; id: string; label: string; sublabel?: string }
  | { kind: "tag"; slug: string; label: string; sublabel?: string };

const RECENT_KEY = "co:cmdk:recent";
const FAVORITES_KEY = "co:cmdk:favorites";
const RECENT_LIMIT = 6;
const SEARCH_LIMIT = 7;
/** How long to wait after the last keystroke before hitting the MCP. */
const DEBOUNCE_MS = 180;

// ---- localStorage helpers (Recent / Favorites) -----------------------------

function readStore(key: string): PaletteTarget[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as PaletteTarget[]) : [];
  } catch {
    return [];
  }
}

function writeStore(key: string, entries: PaletteTarget[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(entries));
  } catch {
    /* storage full / disabled — recents are best-effort */
  }
}

function targetKey(target: PaletteTarget): string {
  return target.kind === "tag" ? `tag:${target.slug}` : `${target.kind}:${target.id}`;
}

/** Push a target to the front of the recent list (dedup, capped). */
export function rememberRecent(target: PaletteTarget) {
  const key = targetKey(target);
  const existing = readStore(RECENT_KEY).filter((entry) => targetKey(entry) !== key);
  writeStore(RECENT_KEY, [target, ...existing].slice(0, RECENT_LIMIT));
}

// ---- debounce hook ----------------------------------------------------------

function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(handle);
  }, [value, delay]);
  return debounced;
}

// ---- icon + row helpers -----------------------------------------------------

const KIND_ICON: Record<PaletteTarget["kind"], LucideIcon> = {
  person: Users,
  org: Building2,
  tag: TagsIcon
};

const KIND_ACCENT: Record<PaletteTarget["kind"], string> = {
  person: "text-[oklch(var(--co-sky-500))]",
  org: "text-[oklch(var(--co-emerald-500))]",
  tag: "text-[oklch(var(--co-amber-500))]"
};

function ResultRow({
  target,
  meta,
  onRun
}: {
  target: PaletteTarget;
  meta?: string;
  onRun: () => void;
}) {
  const Icon = KIND_ICON[target.kind];
  // A stable, unique value so cmdk selection never collides across groups.
  const value = `${targetKey(target)} ${target.label} ${target.sublabel ?? ""}`;
  return (
    <Command.Item
      value={value}
      onSelect={onRun}
      className={cn(
        "flex cursor-pointer items-center gap-3 rounded-[var(--radius-sm)] px-2 py-2 text-sm text-foreground",
        "aria-selected:bg-primary/12 aria-selected:text-foreground"
      )}
    >
      <span
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted",
          KIND_ACCENT[target.kind]
        )}
      >
        <Icon className="h-4 w-4" strokeWidth={1.8} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{target.label}</span>
        {target.sublabel ? (
          <span className="block truncate text-xs text-muted-foreground">{target.sublabel}</span>
        ) : null}
      </span>
      {meta ? <span className="shrink-0 text-xs capitalize text-muted-foreground">{meta}</span> : null}
    </Command.Item>
  );
}

// ---- quick actions ----------------------------------------------------------

type QuickAction = {
  id: string;
  label: string;
  keywords: string;
  icon: LucideIcon;
  run: (navigate: ReturnType<typeof useNavigate>) => void;
};

const QUICK_ACTIONS: QuickAction[] = [
  {
    id: "add-person",
    label: "Add person",
    keywords: "new create contact people",
    icon: UserPlus,
    run: (navigate) => void navigate({ to: "/people" })
  },
  {
    id: "new-org",
    label: "New organization",
    keywords: "add create company org business",
    icon: Building2,
    run: (navigate) => void navigate({ to: "/orgs" })
  },
  {
    id: "import-contacts",
    label: "Import contacts",
    keywords: "csv vcard upload bulk",
    icon: Upload,
    run: (navigate) => void navigate({ to: "/settings" })
  },
  {
    id: "review-queue",
    label: "Open Review Queue",
    keywords: "inbox proposals approvals pending",
    icon: ListChecks,
    run: (navigate) => void navigate({ to: "/review" })
  }
];

function matchesQuery(haystack: string, query: string): boolean {
  return haystack.toLowerCase().includes(query.trim().toLowerCase());
}

export type CommandPaletteProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate();
  const token = useAccessToken();
  const qk = useQueryKeys();
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, DEBOUNCE_MS);
  const trimmed = debouncedQuery.trim();
  const hasQuery = trimmed.length > 0;
  const searchEnabled = open && hasQuery && Boolean(token);

  // Reset the input each time the palette closes so it always opens fresh.
  const prevOpen = useRef(open);
  useEffect(() => {
    if (prevOpen.current && !open) setQuery("");
    prevOpen.current = open;
  }, [open]);

  // Recent + Favorites are read each time the palette opens (localStorage is sync).
  const [recents, setRecents] = useState<PaletteTarget[]>([]);
  const [favorites, setFavorites] = useState<PaletteTarget[]>([]);
  useEffect(() => {
    if (!open) return;
    setRecents(readStore(RECENT_KEY));
    setFavorites(readStore(FAVORITES_KEY));
  }, [open]);

  // Server-side search. `enabled` gates these so opening the palette (empty
  // query) never fires idle list requests; the debounce throttles keystrokes.
  const peopleQuery = useQuery({
    queryKey: qk.cmdkPeople(trimmed),
    enabled: searchEnabled,
    queryFn: () => tools.searchPeople({ query: trimmed, limit: SEARCH_LIMIT, include_archived: false }, token)
  });
  const orgsQuery = useQuery({
    queryKey: qk.cmdkOrgs(trimmed),
    enabled: searchEnabled,
    queryFn: () => tools.searchOrganizations({ query: trimmed, limit: SEARCH_LIMIT }, token)
  });
  // Tags are a small, fully-loaded set; fetch once while the palette is open.
  const tagsQuery = useQuery({
    queryKey: qk.cmdkTags,
    enabled: open && Boolean(token),
    queryFn: () => tools.listTags(token),
    staleTime: 5 * 60_000
  });

  const peopleResults: PaletteTarget[] = useMemo(
    () =>
      (searchEnabled ? peopleQuery.data?.items ?? [] : []).map((person) => ({
        kind: "person",
        id: person.person_id,
        label: person.display_name,
        sublabel: person.headline ?? person.current_org ?? person.primary_email ?? undefined
      })),
    [searchEnabled, peopleQuery.data]
  );

  const orgResults: PaletteTarget[] = useMemo(
    () =>
      (searchEnabled ? orgsQuery.data?.items ?? [] : []).map((org) => ({
        kind: "org",
        id: org.org_id,
        label: org.display_name,
        sublabel: org.industry ?? org.domain ?? undefined
      })),
    [searchEnabled, orgsQuery.data]
  );

  const tagResults: PaletteTarget[] = useMemo(
    () =>
      hasQuery
        ? (tagsQuery.data?.items ?? [])
            .filter((tag) => matchesQuery(`${tag.display_name ?? ""} ${tag.slug}`, trimmed))
            .slice(0, SEARCH_LIMIT)
            .map((tag) => ({
              kind: "tag",
              slug: tag.slug,
              label: tag.display_name ?? tag.slug,
              sublabel: tag.usage_count != null ? `${tag.usage_count} tagged` : undefined
            }))
        : [],
    [hasQuery, tagsQuery.data, trimmed]
  );

  const visibleActions = hasQuery
    ? QUICK_ACTIONS.filter((action) => matchesQuery(`${action.label} ${action.keywords}`, trimmed))
    : QUICK_ACTIONS;

  const searching = searchEnabled && (peopleQuery.isFetching || orgsQuery.isFetching);
  const noResults =
    hasQuery &&
    !searching &&
    peopleResults.length === 0 &&
    orgResults.length === 0 &&
    tagResults.length === 0 &&
    visibleActions.length === 0;

  const go = useCallback(
    (target: PaletteTarget) => {
      rememberRecent(target);
      onOpenChange(false);
      if (target.kind === "person") void navigate({ to: "/people/$id", params: { id: target.id } });
      else if (target.kind === "org") void navigate({ to: "/orgs/$id", params: { id: target.id } });
      else void navigate({ to: "/tags" });
    },
    [navigate, onOpenChange]
  );

  const runAction = useCallback(
    (action: QuickAction) => {
      onOpenChange(false);
      action.run(navigate);
    },
    [navigate, onOpenChange]
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="co-animate-fade-in fixed inset-0 z-40 bg-background/70 backdrop-blur-sm" />
        <DialogPrimitive.Content
          aria-label="Command palette"
          className={cn(
            "co-animate-fade-in-scale fixed left-1/2 top-[12vh] z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2",
            "overflow-hidden rounded-[var(--radius-lg)] border bg-popover text-popover-foreground shadow-[var(--shadow-4)]"
          )}
          // Let cmdk's input own initial focus; Radix should not pull it back.
          onOpenAutoFocus={(event) => event.preventDefault()}
        >
          <DialogTitle className="sr-only">Command palette</DialogTitle>
          <DialogDescription className="sr-only">
            Search people, organizations, and tags, or run a quick action. Use the arrow keys to move and
            Enter to open.
          </DialogDescription>

          <Command
            label="Command palette"
            shouldFilter={false}
            loop
            className="flex flex-col overflow-hidden bg-popover"
          >
            <div className="flex items-center gap-2.5 border-b px-3.5">
              <Search className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={1.8} aria-hidden />
              <Command.Input
                autoFocus
                value={query}
                onValueChange={setQuery}
                placeholder="Search people, orgs, tags…"
                className="h-12 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
              {searching ? <Spinner size="sm" label="" /> : null}
            </div>

            <Command.List className="co-scrollbar max-h-[min(60vh,420px)] overflow-y-auto p-2">
              {noResults ? (
                <Command.Empty className="px-3 py-10 text-center text-sm text-muted-foreground">
                  No matches for <span className="font-medium text-foreground">“{trimmed}”</span>.
                </Command.Empty>
              ) : null}

              {/* ---- Empty-query mode: Favorites + Recent ---- */}
              {!hasQuery && favorites.length ? (
                <PaletteGroup heading="Favorites">
                  {favorites.map((target) => (
                    <ResultRow
                      key={`fav-${targetKey(target)}`}
                      target={target}
                      meta={target.kind}
                      onRun={() => go(target)}
                    />
                  ))}
                </PaletteGroup>
              ) : null}

              {!hasQuery && recents.length ? (
                <PaletteGroup heading="Recent">
                  {recents.map((target) => (
                    <ResultRow
                      key={`recent-${targetKey(target)}`}
                      target={target}
                      meta={target.kind}
                      onRun={() => go(target)}
                    />
                  ))}
                </PaletteGroup>
              ) : null}

              {/* ---- Search results ---- */}
              {peopleResults.length ? (
                <PaletteGroup heading="People">
                  {peopleResults.map((target) => (
                    <ResultRow key={targetKey(target)} target={target} onRun={() => go(target)} />
                  ))}
                </PaletteGroup>
              ) : null}

              {orgResults.length ? (
                <PaletteGroup heading="Organizations">
                  {orgResults.map((target) => (
                    <ResultRow key={targetKey(target)} target={target} onRun={() => go(target)} />
                  ))}
                </PaletteGroup>
              ) : null}

              {tagResults.length ? (
                <PaletteGroup heading="Tags">
                  {tagResults.map((target) => (
                    <ResultRow key={targetKey(target)} target={target} onRun={() => go(target)} />
                  ))}
                </PaletteGroup>
              ) : null}

              {/* ---- Quick actions ---- */}
              {visibleActions.length ? (
                <PaletteGroup heading="Quick actions">
                  {visibleActions.map((action) => (
                    <Command.Item
                      key={action.id}
                      value={`action ${action.label} ${action.keywords}`}
                      onSelect={() => runAction(action)}
                      className={cn(
                        "flex cursor-pointer items-center gap-3 rounded-[var(--radius-sm)] px-2 py-2 text-sm text-foreground",
                        "aria-selected:bg-primary/12 aria-selected:text-foreground"
                      )}
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                        <action.icon className="h-4 w-4" strokeWidth={1.8} />
                      </span>
                      <span className="flex-1 truncate font-medium">{action.label}</span>
                      <Plus className="h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.8} aria-hidden />
                    </Command.Item>
                  ))}
                </PaletteGroup>
              ) : null}
            </Command.List>

            <PaletteFooter />
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </Dialog>
  );
}

function PaletteGroup({ heading, children }: { heading: string; children: React.ReactNode }) {
  return (
    <Command.Group
      heading={heading}
      className={cn(
        "mb-1 last:mb-0",
        "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2",
        "[&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-semibold",
        "[&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider",
        "[&_[cmdk-group-heading]]:text-muted-foreground"
      )}
    >
      {children}
    </Command.Group>
  );
}

/** The thin keycap legend, mirroring the inbox palette footer language. */
function PaletteFooter() {
  return (
    <div className="flex items-center justify-between gap-3 border-t bg-muted/40 px-3.5 py-2 text-[11px] text-muted-foreground">
      <span className="inline-flex items-center gap-1.5">
        <Keycap>↑</Keycap>
        <Keycap>↓</Keycap>
        <span>navigate</span>
      </span>
      <span className="inline-flex items-center gap-1.5">
        <Keycap>↵</Keycap>
        <span>open</span>
        <span className="px-1 text-muted-foreground/50">·</span>
        <Keycap>esc</Keycap>
        <span>close</span>
      </span>
    </div>
  );
}

function Keycap({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex min-w-[1.25rem] items-center justify-center rounded-[var(--radius-sm)] border border-border bg-card px-1 py-0.5 font-mono text-[10px] font-medium leading-none text-muted-foreground">
      {children}
    </kbd>
  );
}
