/**
 * QualityFilterChips — the bulk-approve filter rail above the proposal list.
 *
 * Drains the connector queue fast: pick a confidence floor, narrow to has-
 * email/has-phone, optionally restrict by provider, and the Inbox shows only
 * proposals that match. Composed with focus mode + conflicts-only.
 *
 * State convention: chips are URL-driven (search params) so an "approve all
 * link" can be shared / bookmarked. The parent owns the param parsing — this
 * component just renders the state + emits a single `onChange` event with a
 * stable object shape.
 *
 * Compose rules baked into the parent's filter function:
 *   - `confMin` is single-pick (0.95 / 0.90 / 0.80 / null)
 *   - `hasEmail` AND `hasPhone` are independent toggles (AND'd)
 *   - `providers` are multi-select (OR'd)
 */
import { Mail, Phone, ShieldCheck, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MonoNumeric } from "@/design-system";
import { cn } from "@/lib/utils";
import type { Proposal } from "@/lib/types";

export type ConnectorProviderFilter = "gmail" | "m365" | "icloud";

export type QualityFilterState = {
  /** Minimum confidence threshold (single-pick). null = no floor. */
  confMin: 0.95 | 0.9 | 0.8 | null;
  hasEmail: boolean;
  hasPhone: boolean;
  /** Multi-select provider chips; empty = any. */
  providers: ConnectorProviderFilter[];
};

export const EMPTY_QUALITY_FILTER: QualityFilterState = {
  confMin: null,
  hasEmail: false,
  hasPhone: false,
  providers: []
};

export function qualityFilterIsEmpty(f: QualityFilterState): boolean {
  return (
    f.confMin == null && !f.hasEmail && !f.hasPhone && f.providers.length === 0
  );
}

// Heuristic: look at agent_id + actor_chain + payload metadata to decide which
// connector a proposal came from. The Codex backend writes `vcard_import` /
// `connector_pull` action_types with `source.action_subtype` carrying the
// provider; the older path stamps `agent_id` with the connector slug. We
// accept either.
export function detectProvider(p: Proposal): ConnectorProviderFilter | null {
  // 1. Direct agent slug match (most common after Phase 4 connectors land).
  const slug = (p.agent_id ?? "").toLowerCase();
  if (slug.includes("gmail")) return "gmail";
  if (slug.includes("m365") || slug.includes("microsoft")) return "m365";
  if (slug.includes("icloud") || slug.includes("apple")) return "icloud";

  // 2. action_type marker (e.g. "vcard_import").
  const action = (p.action_type ?? "").toLowerCase();
  if (action.includes("gmail")) return "gmail";
  if (action.includes("m365")) return "m365";
  if (action.includes("icloud")) return "icloud";

  // 3. payload_after.source.action_subtype (the canonical Phase 4 marker).
  const after = p.payload_after as Record<string, unknown> | null;
  const source = after?.source as { action_subtype?: string } | undefined;
  const subtype = (source?.action_subtype ?? "").toLowerCase();
  if (subtype === "gmail") return "gmail";
  if (subtype === "m365") return "m365";
  if (subtype === "icloud") return "icloud";

  return null;
}

function hasNonEmptyArray(payload: Record<string, unknown> | null, key: string): boolean {
  if (!payload) return false;
  const v = payload[key];
  return Array.isArray(v) && v.length > 0;
}

export function proposalMatchesQualityFilter(
  p: Proposal,
  f: QualityFilterState
): boolean {
  if (f.confMin != null && p.confidence < f.confMin) return false;
  if (f.hasEmail && !hasNonEmptyArray(p.payload_after, "emails")) return false;
  if (f.hasPhone && !hasNonEmptyArray(p.payload_after, "phones")) return false;
  if (f.providers.length > 0) {
    const provider = detectProvider(p);
    if (!provider || !f.providers.includes(provider)) return false;
  }
  return true;
}

type Props = {
  value: QualityFilterState;
  matchedCount: number;
  totalCount: number;
  onChange: (next: QualityFilterState) => void;
};

const CONF_OPTIONS: { label: string; value: 0.95 | 0.9 | 0.8 }[] = [
  { label: "≥ 0.95", value: 0.95 },
  { label: "≥ 0.90", value: 0.9 },
  { label: "≥ 0.80", value: 0.8 }
];

const PROVIDER_OPTIONS: { label: string; value: ConnectorProviderFilter }[] = [
  { label: "Gmail", value: "gmail" },
  { label: "M365", value: "m365" },
  { label: "iCloud", value: "icloud" }
];

export function QualityFilterChips({ value, matchedCount, totalCount, onChange }: Props) {
  const empty = qualityFilterIsEmpty(value);
  const hasBoth = value.hasEmail && value.hasPhone;

  const setConfMin = (next: 0.95 | 0.9 | 0.8) =>
    onChange({ ...value, confMin: value.confMin === next ? null : next });

  const toggleHasEmail = () => onChange({ ...value, hasEmail: !value.hasEmail });
  const toggleHasPhone = () => onChange({ ...value, hasPhone: !value.hasPhone });
  const toggleHasBoth = () => {
    const next = !hasBoth;
    onChange({ ...value, hasEmail: next, hasPhone: next });
  };

  const toggleProvider = (p: ConnectorProviderFilter) => {
    const set = new Set(value.providers);
    if (set.has(p)) set.delete(p);
    else set.add(p);
    onChange({ ...value, providers: Array.from(set) });
  };

  return (
    <div
      className="flex flex-wrap items-center gap-co-6 border-b border-border bg-card/35 px-co-16 py-co-8"
      role="toolbar"
      aria-label="Quality filters"
    >
      <span className="font-mono text-11 font-semibold uppercase text-muted-foreground">
        Quality
      </span>

      {/* Confidence threshold (single-pick) */}
      <span aria-hidden className="h-4 w-px bg-border" />
      <span className="flex items-center gap-co-4 text-12 text-muted-foreground">
        <ShieldCheck className="h-3 w-3" />
        Confidence
      </span>
      <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Minimum confidence">
        {CONF_OPTIONS.map((opt) => {
          const active = value.confMin === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => setConfMin(opt.value)}
              role="radio"
              aria-checked={active}
              className={cn(
                "focus-ring h-7 rounded-full border px-2.5 text-xs font-medium transition-colors",
                active
                  ? "border-success/60 bg-success/15 text-success"
                  : "border-border bg-background text-muted-foreground hover:bg-muted"
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>

      {/* Field-presence (AND'd) */}
      <span aria-hidden className="h-4 w-px bg-border" />
      <button
        type="button"
        onClick={toggleHasEmail}
        aria-pressed={value.hasEmail}
        className={cn(
          "focus-ring inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-xs font-medium transition-colors",
          value.hasEmail
            ? "border-primary/60 bg-primary/15 text-link"
            : "border-border bg-background text-muted-foreground hover:bg-muted"
        )}
      >
        <Mail className="h-3 w-3" />
        Has email
      </button>
      <button
        type="button"
        onClick={toggleHasPhone}
        aria-pressed={value.hasPhone}
        className={cn(
          "focus-ring inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-xs font-medium transition-colors",
          value.hasPhone
            ? "border-primary/60 bg-primary/15 text-link"
            : "border-border bg-background text-muted-foreground hover:bg-muted"
        )}
      >
        <Phone className="h-3 w-3" />
        Has phone
      </button>
      <button
        type="button"
        onClick={toggleHasBoth}
        aria-pressed={hasBoth}
        className={cn(
          "focus-ring h-7 rounded-full border px-2.5 text-xs font-medium transition-colors",
          hasBoth
            ? "border-primary/60 bg-primary/15 text-link"
            : "border-border bg-background text-muted-foreground hover:bg-muted"
        )}
        title="Has email AND has phone"
      >
        Has both
      </button>

      {/* Provider (OR'd) */}
      <span aria-hidden className="h-4 w-px bg-border" />
      <span className="text-12 text-muted-foreground">From</span>
      {PROVIDER_OPTIONS.map((opt) => {
        const active = value.providers.includes(opt.value);
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => toggleProvider(opt.value)}
            aria-pressed={active}
            className={cn(
              "focus-ring h-7 rounded-full border px-2.5 text-xs font-medium transition-colors",
              active
                ? "border-accent/60 bg-accent/15 text-foreground"
                : "border-border bg-background text-muted-foreground hover:bg-muted"
            )}
          >
            {opt.label}
          </button>
        );
      })}

      {/* Live match count / clear */}
      <div className="ml-auto flex items-center gap-co-6">
        {!empty ? (
          <>
            <Badge
              variant="outline"
              className="gap-co-4 rounded-[var(--radius-sm)]"
              aria-live="polite"
              data-testid="quality-match-count"
            >
              <MonoNumeric tone="strong">{matchedCount}</MonoNumeric>
              <span className="text-muted-foreground">match{matchedCount === 1 ? "" : "es"}</span>
              <span className="text-muted-foreground">/</span>
              <MonoNumeric tone="muted">{totalCount}</MonoNumeric>
            </Badge>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onChange(EMPTY_QUALITY_FILTER)}
              aria-label="Clear quality filters"
              className="h-7 gap-1 text-xs"
            >
              <X className="h-3 w-3" />
              Clear
            </Button>
          </>
        ) : (
          <span className="font-mono text-11 text-muted-foreground">
            <MonoNumeric tone="muted">{totalCount}</MonoNumeric>{" "}
            <span>proposals total</span>
          </span>
        )}
      </div>
    </div>
  );
}
