/**
 * StructuredDiff — three-column field diff for dedup + multi-field enrich.
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
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
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
      <p className="text-sm text-muted-foreground italic">
        No structured changes proposed.
      </p>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border">
      <div className="grid grid-cols-[1fr_1fr_min-content] border-b bg-muted/40 text-xs font-medium text-muted-foreground">
        <div className="px-3 py-2">master (current)</div>
        <div className="px-3 py-2 border-l">proposed</div>
        <div className="px-3 py-2 border-l">choice</div>
      </div>
      {fields.map((entry) => {
        const { isAutoMerged, isConflict, isDecideRequired } = classify(entry);
        const chosen = choices[entry.field] ?? entry.agentRecommendation ?? null;
        return (
          <div
            key={entry.field}
            className={cn(
              "group grid grid-cols-[1fr_1fr_min-content] items-stretch text-sm relative",
              isConflict
                ? "bg-amber-100/60 dark:bg-amber-900/30"
                : "bg-background",
              isAutoMerged && "opacity-80",
              isDecideRequired && "border-l-4 border-l-amber-500",
            )}
          >
            <div className="px-3 py-2">
              <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
                {entry.field}
              </p>
              <p className={cn(
                "mt-0.5 break-words",
                isConflict && chosen === "master" && "font-semibold",
              )}>
                {formatValue(entry.masterValue)}
              </p>
            </div>
            <div className="border-l px-3 py-2 flex items-start gap-2">
              <div className="flex-1">
                <p className={cn(
                  "break-words",
                  isConflict && chosen === "proposed" && "font-semibold",
                )}>
                  {formatValue(entry.proposedValue)}
                </p>
                {entry.agentRecommendation && (
                  <p className="mt-1 inline-flex items-center gap-0.5 text-[10px] text-violet-600">
                    <span>agent</span>
                    <ArrowRight className="h-2.5 w-2.5" />
                    <span>{entry.agentRecommendation}</span>
                  </p>
                )}
              </div>
            </div>
            <div className="border-l px-3 py-2 flex items-center gap-1.5">
              {isAutoMerged ? (
                <span className="text-[10px] text-muted-foreground italic">auto</span>
              ) : isDecideRequired ? (
                <span className="text-[10px] font-medium text-amber-600">decide</span>
              ) : null}
              {perFieldChoice && !isAutoMerged && (
                <fieldset
                  className="inline-flex gap-1.5 text-[10px]"
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
                      className="h-3 w-3"
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
                      className="h-3 w-3"
                    />
                    P
                  </label>
                </fieldset>
              )}
            </div>
            {entry.evidenceLabel && (
              <div className="pointer-events-none absolute left-3 top-full z-10 mt-1 hidden rounded-md border bg-popover px-2 py-1 text-[11px] text-popover-foreground shadow-md group-hover:block">
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
