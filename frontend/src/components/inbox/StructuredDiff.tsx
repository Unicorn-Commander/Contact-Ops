/**
 * StructuredDiff - three-column field diff for dedup + multi-field enrich.
 *
 * Columns: master | proposed | choice (radio per-field for T3; accept/reject
 * for T2 and below).
 *
 * Visual states per field row:
 *   auto-merged: dotted bottom border + "auto" faded badge
 *   agent recommended: solid bottom border + "agent ►" indicator
 *   agent declined to pick: double-line + amber bg + "decide" badge
 *   conflict (both sides have differing values): amber bg + "conflict" badge
 *
 * Per-field hover reveals a small evidence panel built with CSS group-hover
 * (no popover library; keeps the dep footprint small per prompt rules).
 *
 * Caller controls `choices` state externally so T3 approval gating can
 * inspect "is every decide-row chosen?" before enabling Approve.
 */
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { MonoNumeric } from "@/design-system";

export type FieldChoice = "master" | "proposed" | "custom";

export type StructuredFieldEntry = {
  field: string;
  masterValue: unknown;
  proposedValue: unknown;
  /** "master" | "proposed" | null (agent declined to pick) */
  agentRecommendation?: "master" | "proposed" | null;
  /** Evidence label hovered on the row */
  evidenceLabel?: string;
};

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "-";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function shouldUseMono(field: string, value: unknown): boolean {
  if (typeof value === "number") return true;
  const normalized = field.toLowerCase();
  if (/(^|_)(id|ids|count|score|confidence|tokens?|cost|rank|index)$/.test(normalized)) return true;
  if (typeof value !== "string") return false;
  return /^[0-9a-f]{8}-[0-9a-f-]{13,}$/i.test(value) || /^\+?\d[\d\s().-]+$/.test(value);
}

function FormattedValue({ field, value }: { field: string; value: unknown }) {
  const formatted = formatValue(value);
  if (shouldUseMono(field, value)) {
    return <MonoNumeric className="break-all">{formatted}</MonoNumeric>;
  }
  return <>{formatted}</>;
}

function classify(entry: StructuredFieldEntry): {
  isAutoMerged: boolean;
  isConflict: boolean;
  isDecideRequired: boolean;
} {
  const equal = formatValue(entry.masterValue) === formatValue(entry.proposedValue);
  const isAutoMerged = equal;
  const isConflict = !equal && entry.masterValue !== null && entry.masterValue !== undefined;
  const isDecideRequired = !equal && entry.agentRecommendation == null;
  return { isAutoMerged, isConflict, isDecideRequired };
}

export type StructuredDiffProps = {
  fields: StructuredFieldEntry[];
  /** Current per-field choices. */
  choices: Record<string, FieldChoice>;
  /** When true: render radio per-field (T3 dedup). When false: master is locked. */
  perFieldChoice: boolean;
  /** True for T2/T1 read-only mode. */
  readOnly?: boolean;
  onChange?: (field: string, choice: FieldChoice) => void;
};

export function StructuredDiff({
  fields,
  choices,
  perFieldChoice,
  readOnly = false,
  onChange,
}: StructuredDiffProps) {
  if (fields.length === 0) {
    return (
      <p className="text-13 text-muted-foreground italic">
        No structured changes proposed.
      </p>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card shadow-[var(--shadow-1)]">
      <div className="grid grid-cols-[1fr_1fr_min-content] border-b border-border bg-muted/45 text-12 font-medium text-muted-foreground">
        <div className="px-co-12 py-co-8">master (current)</div>
        <div className="border-l border-border px-co-12 py-co-8">proposed</div>
        <div className="border-l border-border px-co-12 py-co-8">choice</div>
      </div>
      {fields.map((entry) => {
        const { isAutoMerged, isConflict, isDecideRequired } = classify(entry);
        const chosen = choices[entry.field] ?? entry.agentRecommendation ?? null;
        return (
          <div
            key={entry.field}
            className={cn(
              "group relative grid grid-cols-[1fr_1fr_min-content] items-stretch text-13",
              isConflict
                ? "bg-warning/10"
                : "bg-background",
              isDecideRequired && "border-l-4 border-l-warning",
            )}
          >
            <div className="px-co-12 py-co-8">
              <p className="font-mono text-11 uppercase text-muted-foreground">
                {entry.field}
              </p>
              <p className={cn(
                "mt-co-2 break-words",
                isConflict && chosen === "master" && "font-semibold",
              )}>
                <FormattedValue field={entry.field} value={entry.masterValue} />
              </p>
            </div>
            <div className="flex items-start gap-co-8 border-l border-border px-co-12 py-co-8">
              <div className="flex-1">
                <p className={cn(
                  "break-words",
                  isConflict && chosen === "proposed" && "font-semibold",
                )}>
                  <FormattedValue field={entry.field} value={entry.proposedValue} />
                </p>
                {entry.agentRecommendation && (
                  <p className="mt-co-4 inline-flex items-center gap-co-2 rounded-[var(--radius-sm)] border border-primary/30 bg-primary/10 px-co-4 py-co-2 font-mono text-11 text-link">
                    <span>agent</span>
                    <ArrowRight className="h-2.5 w-2.5" />
                    <span>{entry.agentRecommendation}</span>
                  </p>
                )}
              </div>
            </div>
            <div className="flex items-center gap-co-6 border-l border-border px-co-12 py-co-8">
              {isAutoMerged ? (
                <span className="text-11 text-muted-foreground italic">auto</span>
              ) : isDecideRequired ? (
                <span className="text-11 font-medium text-warning">decide</span>
              ) : null}
              {perFieldChoice && !isAutoMerged && (
                <fieldset
                  className="inline-flex gap-co-6 text-11"
                  disabled={readOnly}
                  aria-label={`Choice for field ${entry.field}`}
                >
                  <label className="inline-flex cursor-pointer items-center gap-1">
                    <input
                      type="radio"
                      name={`field-${entry.field}`}
                      value="master"
                      checked={chosen === "master"}
                      onChange={() => onChange?.(entry.field, "master")}
                      className="h-3 w-3 accent-primary"
                    />
                    M
                  </label>
                  <label className="inline-flex cursor-pointer items-center gap-1">
                    <input
                      type="radio"
                      name={`field-${entry.field}`}
                      value="proposed"
                      checked={chosen === "proposed"}
                      onChange={() => onChange?.(entry.field, "proposed")}
                      className="h-3 w-3 accent-primary"
                    />
                    P
                  </label>
                </fieldset>
              )}
            </div>
            {entry.evidenceLabel && (
              <div className="pointer-events-none absolute left-co-12 top-full z-10 mt-co-4 hidden rounded-md border border-border bg-popover px-co-8 py-co-4 text-11 text-popover-foreground shadow-[var(--shadow-2)] group-hover:block">
                {entry.evidenceLabel}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Derive StructuredFieldEntries from a proposal's payload_before /
 * payload_after. Used by the detail pane when rendering a non-dedup
 * multi-field enrichment.
 */
export function entriesFromPayload(
  payload_before: Record<string, unknown> | null,
  payload_after: Record<string, unknown>,
  recommendedChoices?: Record<string, "master" | "proposed" | null>,
): StructuredFieldEntry[] {
  const before = payload_before ?? {};
  const keys = new Set([...Object.keys(before), ...Object.keys(payload_after)]);
  return Array.from(keys).map((field) => ({
    field,
    masterValue: before[field] ?? null,
    proposedValue: payload_after[field] ?? null,
    agentRecommendation: recommendedChoices?.[field] ?? null,
  }));
}
