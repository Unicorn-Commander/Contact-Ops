/**
 * Search-to-root control for the Graph page.
 *
 * Replaces the old raw "paste a person UUID" field as the PRIMARY way to root
 * the ego graph. The user types a name → debounced MCP `search_people` (the
 * exact same tool the ⌘K palette and the People directory use) → picking a
 * result roots the graph on that person.
 *
 * A "paste an ID" advanced affordance is kept as a cheap secondary path,
 * collapsed by default so it never competes with the friendly search.
 *
 * Pure controlled component: it owns only the transient query/UI state and
 * calls `onPick(personId, displayName)` when the user commits a root.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, Search, UserRound, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/design-system/Spinner";
import { useAccessToken, useQueryKeys } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import { cn } from "@/lib/utils";

const DEBOUNCE_MS = 180;
const SEARCH_LIMIT = 8;
// Loose UUID shape — enough to keep the advanced "paste an ID" path from firing
// a doomed graph request on obvious typos, without rejecting non-canonical ids.
const UUID_RE = /^[0-9a-fA-F-]{8,}$/;

function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(handle);
  }, [value, delay]);
  return debounced;
}

export function GraphRootPicker({
  rootId,
  rootName,
  onPick,
  onClear,
  autoFocus = false
}: {
  /** Currently-rooted person id, if any (drives the "rooted on" chip). */
  rootId?: string;
  /** Best-known display name for the current root (search result or bootstrap). */
  rootName?: string;
  onPick: (personId: string, displayName?: string) => void;
  /** Clear the current root and return to the search-first state. */
  onClear?: () => void;
  autoFocus?: boolean;
}) {
  const token = useAccessToken();
  const qk = useQueryKeys();
  const [query, setQuery] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [pastedId, setPastedId] = useState("");
  const debounced = useDebouncedValue(query, DEBOUNCE_MS);
  const trimmed = debounced.trim();
  const hasQuery = trimmed.length > 0;
  const searchEnabled = hasQuery && Boolean(token);

  const search = useQuery({
    queryKey: qk.graphRootSearch(trimmed),
    enabled: searchEnabled,
    queryFn: () => tools.searchPeople({ query: trimmed, limit: SEARCH_LIMIT, include_archived: false }, token)
  });

  const results = useMemo(() => (searchEnabled ? search.data?.items ?? [] : []), [searchEnabled, search.data]);
  const searching = searchEnabled && search.isFetching;
  const noResults = hasQuery && !searching && results.length === 0;

  // Close the results list on outside click so it behaves like a typeahead.
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const commit = (personId: string, displayName?: string) => {
    onPick(personId, displayName);
    setQuery("");
    setOpen(false);
  };

  const pastedValid = pastedId.trim().length > 0 && UUID_RE.test(pastedId.trim());

  return (
    <div className="space-y-2">
      <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground" htmlFor="graph-root-search">
        Root person
      </label>

      {rootId ? (
        // Compact "rooted on" chip with a one-click re-pick / clear affordance.
        <div className="flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-sm shadow-[var(--shadow-1)]">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <UserRound className="h-3.5 w-3.5" strokeWidth={1.9} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium text-foreground">{rootName ?? "Selected person"}</span>
            <span className="block truncate text-[11px] text-muted-foreground">Ego network rooted here</span>
          </span>
          {onClear ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 text-xs text-muted-foreground"
              onClick={onClear}
            >
              <X className="h-3.5 w-3.5" strokeWidth={2} />
              Change
            </Button>
          ) : null}
        </div>
      ) : null}

      <div ref={wrapRef} className="relative">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          strokeWidth={1.7}
          aria-hidden
        />
        <Input
          id="graph-root-search"
          autoFocus={autoFocus}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder={rootId ? "Search to root on someone else…" : "Search a person to explore their network…"}
          className="pl-9"
          role="combobox"
          aria-expanded={open && hasQuery}
          aria-controls="graph-root-results"
          autoComplete="off"
        />
        {searching ? (
          <span className="absolute right-3 top-1/2 -translate-y-1/2">
            <Spinner size="sm" label="" />
          </span>
        ) : null}

        {open && hasQuery ? (
          <div
            id="graph-root-results"
            role="listbox"
            className="co-animate-fade-in co-scrollbar absolute left-0 right-0 top-[calc(100%+0.375rem)] z-30 max-h-72 overflow-y-auto rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-[var(--shadow-4)]"
          >
            {results.map((person) => {
              const sub = person.headline ?? person.current_org ?? person.primary_email ?? undefined;
              const active = person.person_id === rootId;
              return (
                <button
                  key={person.person_id}
                  type="button"
                  role="option"
                  aria-selected={active}
                  onClick={() => commit(person.person_id, person.display_name)}
                  className={cn(
                    "focus-ring flex w-full items-center gap-3 rounded-[var(--radius-sm)] px-2 py-2 text-left text-sm text-foreground",
                    "hover:bg-primary/10",
                    active && "bg-primary/10"
                  )}
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted text-[oklch(var(--co-sky-500))]">
                    <UserRound className="h-4 w-4" strokeWidth={1.8} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{person.display_name}</span>
                    {sub ? <span className="block truncate text-xs text-muted-foreground">{sub}</span> : null}
                  </span>
                  {active ? <Check className="h-4 w-4 shrink-0 text-primary" strokeWidth={2.2} /> : null}
                </button>
              );
            })}

            {noResults ? (
              <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                No people match <span className="font-medium text-foreground">“{trimmed}”</span>.
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* Secondary / advanced: paste a graph id directly. Collapsed by default. */}
      <div className="pt-0.5">
        <button
          type="button"
          onClick={() => setAdvancedOpen((value) => !value)}
          aria-expanded={advancedOpen}
          className="focus-ring inline-flex items-center gap-1 rounded text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronDown
            className={cn("h-3.5 w-3.5 transition-transform", advancedOpen && "rotate-180")}
            strokeWidth={2}
          />
          Advanced: paste a person ID
        </button>
        {advancedOpen ? (
          <form
            className="co-animate-fade-in mt-2 flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (pastedValid) {
                commit(pastedId.trim());
                setPastedId("");
              }
            }}
          >
            <Input
              value={pastedId}
              onChange={(event) => setPastedId(event.target.value)}
              placeholder="Person UUID"
              className="h-8 font-mono text-xs"
              aria-label="Person UUID"
              autoComplete="off"
              spellCheck={false}
            />
            <Button type="submit" size="sm" disabled={!pastedValid} className="h-8 shrink-0">
              Root
            </Button>
          </form>
        ) : null}
      </div>
    </div>
  );
}
