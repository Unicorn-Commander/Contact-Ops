/**
 * ToolCallCard — inline preview of one MCP tool the assistant invoked.
 *
 * Shows the tool name with a small wrench/sparkles icon, a single-line summary
 * of the arguments, and a folded preview of the result. When the result
 * references entities (people / orgs), they render as router links so the user
 * can pivot straight to the detail page.
 *
 * Three visual states:
 *   - pending  (no result yet)      → spinner row, dashed border
 *   - resolved (result is present)  → solid border, entity chips
 *   - error    (error field set)    → rose-tinted border + message
 *
 * The card is presentational; the upstream hook owns the call lifecycle.
 */
import { Link } from "@tanstack/react-router";
import { AlertCircle, ChevronDown, ChevronRight, Wrench, Building2, User } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import type { ToolCall } from "@/hooks/useChat";
import { Spinner } from "@/design-system/Spinner";

type EntityChip =
  | { kind: "person"; id: string; label: string }
  | { kind: "org"; id: string; label: string };

const ENTITY_LABEL_KEYS = ["display_name", "label", "name", "legal_name"] as const;

/**
 * Sniff the most common Contact-Ops MCP result shapes and surface any
 * person/org entities the user might want to click through to. Tolerant of
 * unknown shapes — returns an empty list rather than throwing.
 */
function extractEntities(result: unknown): EntityChip[] {
  if (!result || typeof result !== "object") return [];
  const chips: EntityChip[] = [];
  const seen = new Set<string>();

  const consider = (value: unknown): void => {
    if (!value || typeof value !== "object") return;
    const record = value as Record<string, unknown>;
    const personId = typeof record.person_id === "string" ? record.person_id : undefined;
    const orgId = typeof record.org_id === "string" ? record.org_id : undefined;
    let label: string | undefined;
    for (const key of ENTITY_LABEL_KEYS) {
      const candidate = record[key];
      if (typeof candidate === "string" && candidate.trim().length > 0) {
        label = candidate;
        break;
      }
    }
    if (personId && label) {
      const dedupKey = `person:${personId}`;
      if (!seen.has(dedupKey)) {
        seen.add(dedupKey);
        chips.push({ kind: "person", id: personId, label });
      }
      return;
    }
    if (orgId && label) {
      const dedupKey = `org:${orgId}`;
      if (!seen.has(dedupKey)) {
        seen.add(dedupKey);
        chips.push({ kind: "org", id: orgId, label });
      }
    }
  };

  // Common shapes: a list result with `items: [...]`, or the entity itself.
  const obj = result as Record<string, unknown>;
  if (Array.isArray(obj.items)) {
    for (const item of obj.items) consider(item);
  }
  consider(obj);
  // Cap to keep the card compact; users can hover the raw JSON to see more.
  return chips.slice(0, 8);
}

/** Compress an argument record into a one-line "k=v, k=v" summary. */
function summarizeArgs(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "no arguments";
  return entries
    .slice(0, 4)
    .map(([key, value]) => {
      let rendered: string;
      if (value === null) rendered = "null";
      else if (typeof value === "string") rendered = value.length > 28 ? `${value.slice(0, 28)}…` : value;
      else if (typeof value === "number" || typeof value === "boolean") rendered = String(value);
      else if (Array.isArray(value)) rendered = `[${value.length}]`;
      else rendered = "{…}";
      return `${key}: ${rendered}`;
    })
    .join(", ");
}

function EntityChipLink({ chip }: { chip: EntityChip }) {
  if (chip.kind === "person") {
    return (
      <Link
        to="/people/$id"
        params={{ id: chip.id }}
        className="focus-ring inline-flex items-center gap-1 rounded-full border border-border bg-background/60 px-2 py-0.5 text-[11px] font-medium text-foreground transition-colors hover:border-[oklch(var(--primary)/0.5)] hover:text-[oklch(var(--primary))]"
      >
        <User className="h-3 w-3 text-[oklch(var(--co-sky-500))]" strokeWidth={1.8} />
        {chip.label}
      </Link>
    );
  }
  return (
    <Link
      to="/orgs/$id"
      params={{ id: chip.id }}
      className="focus-ring inline-flex items-center gap-1 rounded-full border border-border bg-background/60 px-2 py-0.5 text-[11px] font-medium text-foreground transition-colors hover:border-[oklch(var(--primary)/0.5)] hover:text-[oklch(var(--primary))]"
    >
      <Building2 className="h-3 w-3 text-[oklch(var(--co-emerald-500))]" strokeWidth={1.8} />
      {chip.label}
    </Link>
  );
}

export interface ToolCallCardProps {
  call: ToolCall;
}

export function ToolCallCard({ call }: ToolCallCardProps) {
  const resolved = call.result !== undefined || call.error !== undefined;
  const isError = Boolean(call.error);
  const entities = resolved && !isError ? extractEntities(call.result) : [];
  const [expanded, setExpanded] = useState(false);

  const rawJson =
    call.error !== undefined
      ? call.error
      : call.result !== undefined
        ? JSON.stringify(call.result, null, 2)
        : null;

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
      role="group"
      aria-label={`Tool call ${call.tool}`}
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md",
            isError
              ? "bg-[oklch(var(--co-rose-500)/0.15)] text-[oklch(var(--co-rose-500))]"
              : "bg-[oklch(var(--co-brand-500)/0.15)] text-[oklch(var(--co-brand-500))]"
          )}
          aria-hidden="true"
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
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="focus-ring ml-auto inline-flex items-center gap-0.5 rounded-sm px-1 py-0.5 text-[10.5px] font-medium text-muted-foreground transition-colors hover:text-foreground"
            aria-expanded={expanded}
            aria-controls={`tool-raw-${call.call_id}`}
          >
            {expanded ? (
              <ChevronDown className="h-3 w-3" strokeWidth={1.8} />
            ) : (
              <ChevronRight className="h-3 w-3" strokeWidth={1.8} />
            )}
            <span>{expanded ? "Hide" : "Raw"}</span>
          </button>
        )}
      </div>

      <p className="mt-1 truncate text-[11px] text-muted-foreground" title={summarizeArgs(call.args)}>
        {summarizeArgs(call.args)}
      </p>

      {entities.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {entities.map((chip) => (
            <EntityChipLink key={`${chip.kind}:${chip.id}`} chip={chip} />
          ))}
        </div>
      ) : null}

      {isError ? (
        <p className="mt-1 text-[11px] text-[oklch(var(--co-rose-500))]">{call.error}</p>
      ) : null}

      {expanded && rawJson ? (
        <pre
          id={`tool-raw-${call.call_id}`}
          className="co-scrollbar mt-1.5 max-h-44 overflow-auto rounded border border-border bg-background p-2 font-mono text-[10.5px] leading-snug text-muted-foreground"
        >
          {rawJson}
        </pre>
      ) : null}
    </div>
  );
}
