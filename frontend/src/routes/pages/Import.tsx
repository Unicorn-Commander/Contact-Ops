/**
 * Import — the vCard bulk-upload page.
 *
 * Aaron drops a .vcf exported from iCloud / Mac Contacts / Google / Outlook
 * and every record gets proposed into the Review Queue. The backend track owns
 * the parsing + dedup; this page just chooses a file, posts multipart, and
 * shows the summary.
 *
 * Layout (top → bottom):
 *   1. Heading + subtitle
 *   2. Drop zone (drag-drop + click-to-pick)
 *   3. Dry-run toggle ("Preview only — don't create proposals")
 *   4. Action row (Upload + Cancel)
 *   5. Summary card (parsed / proposed / deduped / skipped + first 5 preview)
 *
 * Error surfaces are inline; no modal interruption. The page honors
 * `prefers-reduced-motion` (only border-color transition on dragover).
 */
import { useCallback, useId, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { Link } from "@tanstack/react-router";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileText,
  Info,
  Layers,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Trash2,
  Upload,
  XCircle,
  Zap
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StatStrip, StatTile } from "@/components/domain/Stats";
import { Spinner } from "@/design-system/Spinner";
import {
  FileTooLargeError,
  ImportTransportError,
  ImportValidationError,
  MAX_VCARD_BYTES,
  useVcardImport,
  type ImportError,
  type ImportPreviewRecord,
  type VcardImportResult
} from "@/hooks/useVcardImport";
import { UnauthorizedError } from "@/lib/mcp";
import { cn } from "@/lib/utils";

// ---- Helpers ---------------------------------------------------------------

/** Tolerant filename extension test — vCard (.vcf/.vcard) or CSV (Google,
 *  LinkedIn). We don't gate strictly on this (the server is the source of
 *  truth), but a soft warning saves a round-trip on an obvious wrong file. */
function looksLikeSupported(file: File): boolean {
  const name = file.name.toLowerCase();
  return (
    name.endsWith(".vcf") || name.endsWith(".vcard") || name.endsWith(".csv")
  );
}

/** Human-readable file size — no third-party dep needed. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kib = bytes / 1024;
  if (kib < 1024) return `${kib.toFixed(kib >= 100 ? 0 : 1)} KiB`;
  const mib = kib / 1024;
  return `${mib.toFixed(mib >= 100 ? 0 : 1)} MiB`;
}

// ---- Toggle (no Switch primitive in /ui yet; styled native checkbox) ------

interface DryRunToggleProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}

function DryRunToggle({ checked, onChange, disabled }: DryRunToggleProps) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className={cn(
        "focus-within:ring-ring/40 group inline-flex w-full max-w-md cursor-pointer items-center justify-between gap-3 rounded-lg border border-border bg-card p-3 text-sm transition-colors",
        checked ? "border-primary/40 bg-primary/5" : "hover:bg-muted",
        disabled && "cursor-not-allowed opacity-60"
      )}
    >
      <span className="flex min-w-0 items-start gap-3">
        <span
          aria-hidden="true"
          className={cn(
            "mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md",
            checked
              ? "bg-primary/15 text-primary"
              : "bg-muted text-muted-foreground"
          )}
        >
          <ShieldAlert className="h-3 w-3" strokeWidth={2} />
        </span>
        <span className="min-w-0 leading-snug">
          <span className="block font-medium text-foreground">
            Preview only (don&apos;t create proposals)
          </span>
          <span className="block text-xs text-muted-foreground">
            Parse the file and show the summary without writing anything to the Review Queue.
          </span>
        </span>
      </span>
      {/* Visually a switch; semantically a checkbox so screen readers + form
          tooling get the right affordance without a Radix wrapper. */}
      <span
        aria-hidden="true"
        className={cn(
          "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors",
          checked
            ? "border-primary bg-primary"
            : "border-border bg-muted"
        )}
      >
        <span
          className={cn(
            "absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 rounded-full bg-background shadow-[var(--shadow-1)] transition-transform",
            checked ? "translate-x-[18px]" : "translate-x-[2px]"
          )}
        />
      </span>
      <input
        id={id}
        type="checkbox"
        role="switch"
        aria-checked={checked}
        className="sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}

// Trust-this-import toggle: high-confidence new rows are created + graphed
// immediately instead of queuing for review. Mirrors DryRunToggle's affordance.
function AutoApproveToggle({ checked, onChange, disabled }: DryRunToggleProps) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className={cn(
        "focus-within:ring-ring/40 group inline-flex w-full max-w-md cursor-pointer items-center justify-between gap-3 rounded-lg border border-border bg-card p-3 text-sm transition-colors",
        checked ? "border-primary/40 bg-primary/5" : "hover:bg-muted",
        disabled && "cursor-not-allowed opacity-60"
      )}
    >
      <span className="flex min-w-0 items-start gap-3">
        <span
          aria-hidden="true"
          className={cn(
            "mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md",
            checked ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground"
          )}
        >
          <Zap className="h-3 w-3" strokeWidth={2} />
        </span>
        <span className="min-w-0 leading-snug">
          <span className="block font-medium text-foreground">
            Trust this import — approve automatically
          </span>
          <span className="block text-xs text-muted-foreground">
            New contacts are created and added to your graph immediately instead of
            waiting in the Review Queue. Duplicates are still skipped.
          </span>
        </span>
      </span>
      <span
        aria-hidden="true"
        className={cn(
          "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors",
          checked ? "border-primary bg-primary" : "border-border bg-muted"
        )}
      >
        <span
          className={cn(
            "absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 rounded-full bg-background shadow-[var(--shadow-1)] transition-transform",
            checked ? "translate-x-[18px]" : "translate-x-[2px]"
          )}
        />
      </span>
      <input
        id={id}
        type="checkbox"
        role="switch"
        aria-checked={checked}
        className="sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}

// ---- Drop zone -------------------------------------------------------------

interface DropZoneProps {
  selected: File | null;
  isDragging: boolean;
  disabled?: boolean;
  onFileChosen: (file: File) => void;
  onDragEnter: () => void;
  onDragLeave: () => void;
  onClear: () => void;
}

function DropZone({
  selected,
  isDragging,
  disabled,
  onFileChosen,
  onDragEnter,
  onDragLeave,
  onClear
}: DropZoneProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const openPicker = useCallback(() => {
    if (disabled) return;
    inputRef.current?.click();
  }, [disabled]);

  const handleFiles = useCallback(
    (list: FileList | null) => {
      if (!list || list.length === 0) return;
      const first = list[0];
      if (first) onFileChosen(first);
    },
    [onFileChosen]
  );

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      onDragLeave();
      if (disabled) return;
      handleFiles(event.dataTransfer.files);
    },
    [disabled, handleFiles, onDragLeave]
  );

  const handleKey = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (disabled) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openPicker();
      }
    },
    [disabled, openPicker]
  );

  const handleChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      handleFiles(event.target.files);
      // Reset the input so picking the same filename twice in a row still fires.
      event.target.value = "";
    },
    [handleFiles]
  );

  return (
    <div className="space-y-3">
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled || undefined}
        aria-label="Drop a .vcf or .csv file here or click to browse"
        onClick={openPicker}
        onKeyDown={handleKey}
        onDragEnter={(event) => {
          event.preventDefault();
          if (!disabled) onDragEnter();
        }}
        onDragOver={(event) => {
          event.preventDefault();
        }}
        onDragLeave={(event) => {
          // Filter out leave events fired by child elements during traversal.
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          onDragLeave();
        }}
        onDrop={handleDrop}
        className={cn(
          "focus-ring relative flex min-h-[200px] cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed bg-card/40 px-6 py-10 text-center transition-colors",
          isDragging
            ? "border-solid border-primary bg-primary/10"
            : "border-border hover:border-primary/60 hover:bg-muted/40",
          disabled && "pointer-events-none opacity-60"
        )}
      >
        <span
          className={cn(
            "bg-gradient-brand co-glow-brand flex h-14 w-14 items-center justify-center rounded-2xl text-primary-foreground transition-transform",
            isDragging && "scale-105"
          )}
          aria-hidden="true"
        >
          <Upload className="h-6 w-6" strokeWidth={1.8} />
        </span>
        <div className="space-y-1.5">
          <p className="text-base font-semibold tracking-tight text-foreground">
            {isDragging ? "Drop to attach" : "Drop a .vcf or .csv file here"}
          </p>
          <p className="text-sm text-muted-foreground">
            or{" "}
            <span className="font-medium text-link underline underline-offset-2">click to choose</span>{" "}
            from your computer
          </p>
        </div>
        <p className="text-xs text-muted-foreground">
          Up to 50 MiB · vCard (.vcf) from iCloud / Mac Contacts · CSV from Google Contacts or LinkedIn
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".vcf,.vcard,.csv,text/vcard,text/x-vcard,text/directory,text/csv"
          className="sr-only"
          onChange={handleChange}
          aria-hidden="true"
          tabIndex={-1}
        />
      </div>

      {/* Selected file summary — sits below the zone, never inside it. */}
      {selected ? (
        <div
          className={cn(
            "flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2.5 shadow-[var(--shadow-1)]"
          )}
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <FileText className="h-4 w-4" strokeWidth={1.8} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-foreground" title={selected.name}>
              {selected.name}
            </p>
            <p className="co-mono-numeric text-xs text-muted-foreground">
              {formatBytes(selected.size)}
              {!looksLikeSupported(selected) ? (
                <span className="ml-2 text-warning">· not a .vcf/.csv — server will validate</span>
              ) : null}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={openPicker} disabled={disabled}>
            <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.8} />
            Replace
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onClear}
            disabled={disabled}
            aria-label="Clear selected file"
            title="Clear selected file"
          >
            <Trash2 className="h-4 w-4" strokeWidth={1.8} />
          </Button>
        </div>
      ) : null}
    </div>
  );
}

// ---- Summary card ----------------------------------------------------------

interface SummaryProps {
  result: VcardImportResult;
  dryRun: boolean;
}

function SummaryCard({ result, dryRun }: SummaryProps) {
  const [errorsOpen, setErrorsOpen] = useState(false);
  const previewSlice = result.preview.slice(0, 5);
  const errorCount = result.errors.length;

  return (
    <div className="space-y-5">
      <Card
        className={cn(
          "border-[oklch(var(--co-emerald-500)/0.35)] bg-[oklch(var(--co-emerald-500)/0.04)]"
        )}
      >
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-md bg-[oklch(var(--co-emerald-500)/0.18)] text-[oklch(var(--co-emerald-500))]">
              <CheckCircle2 className="h-5 w-5" strokeWidth={1.8} />
            </span>
            <div>
              <CardTitle className="text-base">
                {dryRun ? "Preview complete" : "Import complete"}
              </CardTitle>
              <CardDescription className="mt-0.5">
                {dryRun
                  ? "Nothing was written — re-upload with the toggle off to send these to the Review Queue."
                  : result.proposed_count === 0 && (result.duplicate_count ?? 0) > 0
                    ? "Everything in this file is already in your contacts — nothing new to add."
                    : "Records are now in the Review Queue waiting for your approval."}
              </CardDescription>
            </div>
          </div>
          {!dryRun ? (
            <Button asChild variant="outline" size="sm" className="hidden sm:inline-flex">
              <Link to="/review">
                View pending proposals
                <ArrowRight className="h-3.5 w-3.5" strokeWidth={1.8} />
              </Link>
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-5">
          <StatStrip>
            <StatTile label="Parsed" value={result.parsed_count} icon={FileText} tone="sky" hint="records read from file" />
            <StatTile
              label="Proposed"
              value={result.proposed_count}
              icon={Sparkles}
              tone="brand"
              hint={dryRun ? "would be created" : "queued for review"}
            />
            <StatTile
              label="Already in contacts"
              value={result.duplicate_count ?? 0}
              icon={Layers}
              tone="emerald"
              hint="matched a person you already have — skipped"
            />
            <StatTile
              label="Skipped"
              value={result.skipped_count}
              icon={XCircle}
              tone={result.skipped_count > 0 ? "rose" : "amber"}
              hint={result.skipped_count > 0 ? "see errors below" : "none"}
            />
          </StatStrip>

          {/* Preview list — first 5 records */}
          {previewSlice.length > 0 ? (
            <div className="rounded-lg border border-border bg-card">
              <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Preview · first {previewSlice.length} of {result.preview.length}
                </p>
                <Button asChild variant="ghost" size="sm" className="text-link sm:hidden">
                  <Link to="/review">
                    Review Queue
                    <ArrowRight className="h-3.5 w-3.5" strokeWidth={1.8} />
                  </Link>
                </Button>
              </div>
              <ul className="divide-y divide-border">
                {previewSlice.map((record, idx) => (
                  <PreviewRow key={`${record.display_name}-${idx}`} record={record} />
                ))}
              </ul>
            </div>
          ) : null}

          {/* On narrow screens the "View pending proposals" link lives at the
              bottom so the summary doesn't feel cramped. */}
          {!dryRun ? (
            <div className="sm:hidden">
              <Button asChild variant="outline" className="w-full">
                <Link to="/review">
                  View pending proposals
                  <ArrowRight className="h-3.5 w-3.5" strokeWidth={1.8} />
                </Link>
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* Errors collapsible — collapsed by default so a successful import
          with one or two errors stays calm. */}
      {errorCount > 0 ? (
        <Card className="border-[oklch(var(--co-rose-500)/0.35)]">
          <button
            type="button"
            onClick={() => setErrorsOpen((prev) => !prev)}
            aria-expanded={errorsOpen}
            className="focus-ring flex w-full items-center justify-between gap-3 rounded-t-xl px-5 py-4 text-left transition-colors hover:bg-muted/40"
          >
            <div className="flex items-center gap-2.5">
              <span className="flex h-9 w-9 items-center justify-center rounded-md bg-[oklch(var(--co-rose-500)/0.15)] text-[oklch(var(--co-rose-500))]">
                <AlertCircle className="h-5 w-5" strokeWidth={1.8} />
              </span>
              <div>
                <p className="text-sm font-semibold leading-tight">
                  {errorCount} {errorCount === 1 ? "record" : "records"} could not be parsed
                </p>
                <p className="text-xs text-muted-foreground">
                  Click to {errorsOpen ? "hide" : "view"} the parse errors
                </p>
              </div>
            </div>
            {errorsOpen ? (
              <ChevronUp className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
            ) : (
              <ChevronDown className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
            )}
          </button>
          {errorsOpen ? (
            <CardContent>
              <ul className="co-scrollbar max-h-72 space-y-1.5 overflow-y-auto rounded-md border border-border bg-background/60 p-3 font-mono text-xs">
                {result.errors.map((err) => (
                  <ErrorRow key={`${err.index}-${err.reason}`} error={err} />
                ))}
              </ul>
            </CardContent>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

function PreviewRow({ record }: { record: ImportPreviewRecord }) {
  const primaryEmail = record.emails[0];
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5">
      <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
        {record.display_name || <span className="italic text-muted-foreground">No display name</span>}
      </p>
      {record.duplicate ? (
        <span className="inline-flex shrink-0 items-center rounded-full bg-[oklch(var(--co-emerald-500)/0.15)] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[oklch(var(--co-emerald-500))]">
          Already saved
        </span>
      ) : null}
      {record.organization ? (
        <p className="truncate text-xs text-muted-foreground" title={record.organization}>
          {record.organization}
        </p>
      ) : null}
      {primaryEmail ? (
        <p className="truncate font-mono text-xs text-muted-foreground" title={primaryEmail}>
          {primaryEmail}
        </p>
      ) : (
        <p className="text-xs italic text-muted-foreground">no email</p>
      )}
    </li>
  );
}

function ErrorRow({ error }: { error: ImportError }) {
  return (
    <li className="flex gap-3">
      <span className="co-mono-numeric shrink-0 text-muted-foreground">#{error.index}</span>
      <span className="break-words text-foreground/90">{error.reason}</span>
    </li>
  );
}

// ---- Inline error banner ---------------------------------------------------

function ErrorBanner({
  message,
  onRetry,
  retryLabel = "Retry"
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-start gap-3 rounded-lg border border-[oklch(var(--co-rose-500)/0.4)] bg-[oklch(var(--co-rose-500)/0.06)] px-4 py-3 text-sm"
    >
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[oklch(var(--co-rose-500)/0.18)] text-[oklch(var(--co-rose-500))]">
        <AlertCircle className="h-3.5 w-3.5" strokeWidth={2} />
      </span>
      <p className="min-w-0 flex-1 leading-snug text-foreground">{message}</p>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.8} />
          {retryLabel}
        </Button>
      ) : null}
    </div>
  );
}

// ---- Page ------------------------------------------------------------------

export function ImportRoute() {
  const [file, setFile] = useState<File | null>(null);
  const [dryRun, setDryRun] = useState(false);
  const [autoApprove, setAutoApprove] = useState(false);
  const [dragging, setDragging] = useState(false);
  // Drag enter/leave on a parent element fires for every child too; the level
  // counter dedupes that so the styled state doesn't flicker mid-drag.
  const dragDepth = useRef(0);

  const importMutation = useVcardImport();
  const result = importMutation.data ?? null;
  const isUploading = importMutation.isPending;

  // Categorize the error so the banner can branch on cause.
  const errorView = useMemo(() => {
    const err = importMutation.error;
    if (!err) return null;
    if (err instanceof UnauthorizedError) {
      return {
        message: "Your session expired. Redirecting you to sign in…",
        retry: undefined as (() => void) | undefined
      };
    }
    if (err instanceof FileTooLargeError) {
      return { message: err.message, retry: undefined };
    }
    if (err instanceof ImportValidationError) {
      return { message: err.message, retry: undefined };
    }
    if (err instanceof ImportTransportError) {
      return {
        message: `We couldn't reach the import service: ${err.message}`,
        retry: file ? () => importMutation.mutate({ file, dryRun, autoApprove: autoApprove && !dryRun }) : undefined
      };
    }
    return {
      message: err.message || "Something went wrong uploading your file.",
      retry: file ? () => importMutation.mutate({ file, dryRun, autoApprove: autoApprove && !dryRun }) : undefined
    };
  }, [importMutation, file, dryRun, autoApprove]);

  const handleFileChosen = useCallback(
    (next: File) => {
      importMutation.reset();
      setFile(next);
    },
    [importMutation]
  );

  const handleClear = useCallback(() => {
    importMutation.reset();
    setFile(null);
  }, [importMutation]);

  const handleUpload = useCallback(() => {
    if (!file || isUploading) return;
    importMutation.mutate({ file, dryRun, autoApprove: autoApprove && !dryRun });
  }, [file, dryRun, autoApprove, isUploading, importMutation]);

  const handleDragEnter = useCallback(() => {
    dragDepth.current += 1;
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  }, []);

  // Reset dragging if a drop-out fires elsewhere.
  const handleDocumentDrop = useCallback(() => {
    dragDepth.current = 0;
    setDragging(false);
  }, []);

  const oversizedHint =
    file && file.size > MAX_VCARD_BYTES
      ? `Selected file is ${formatBytes(file.size)} — over the 50 MiB cap.`
      : null;

  return (
    <div className="mx-auto max-w-3xl space-y-6" onDrop={handleDocumentDrop}>
      <header className="space-y-2">
        <div className="flex items-center gap-2.5">
          <span className="bg-gradient-brand flex h-9 w-9 items-center justify-center rounded-xl text-primary-foreground shadow-[var(--shadow-1)]">
            <Upload className="h-5 w-5" strokeWidth={1.8} />
          </span>
          <h1 className="text-2xl font-semibold tracking-tight">Import contacts</h1>
        </div>
        <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
          Upload a <span className="font-medium text-foreground">vCard (.vcf)</span> or a{" "}
          <span className="font-medium text-foreground">contacts CSV</span> — Google Contacts, LinkedIn, iCloud,
          Mac Contacts, Outlook, anywhere. Every record gets{" "}
          <span className="font-medium text-foreground">proposed</span> to your Review Queue for approval, and
          anyone already in your contacts is skipped — so a re-import won&apos;t create duplicates.
        </p>

        {/* #10 — explain why this is a manual file upload, not account linking. */}
        <details className="group max-w-2xl rounded-lg border border-border bg-card/50 px-4 py-3 text-sm [&_summary]:cursor-pointer">
          <summary className="flex items-center gap-2 font-medium text-foreground marker:content-none">
            <Info className="h-4 w-4 shrink-0 text-link" strokeWidth={1.8} />
            Why upload a file instead of linking my account?
            <ChevronDown className="ml-auto h-4 w-4 text-muted-foreground transition-transform group-open:rotate-180" strokeWidth={1.8} />
          </summary>
          <div className="mt-3 space-y-2 leading-relaxed text-muted-foreground">
            <p>
              Short version: it&apos;s a pain, and it&apos;s not our fault. Google and Microsoft gate live
              contact access behind a security review that takes weeks plus an annual third-party audit — and
              we&apos;re not going to push your whole address book through that pipeline just to read it once.
              An export <span className="font-medium text-foreground">you</span> control is faster, more
              private, and works <span className="font-medium text-foreground">right now</span>. One-click
              account sync is on the roadmap.
            </p>
            <p className="text-xs">
              <span className="font-medium text-foreground">Get your export:</span>{" "}
              Google Contacts → Export → <span className="font-medium">Google CSV</span> ·{" "}
              LinkedIn → Settings → Get a copy of your data ·{" "}
              iCloud / Mac Contacts / Outlook → export <span className="font-medium">vCard (.vcf)</span>.
            </p>
          </div>
        </details>
      </header>

      <DropZone
        selected={file}
        isDragging={dragging}
        disabled={isUploading}
        onFileChosen={handleFileChosen}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onClear={handleClear}
      />

      {oversizedHint ? <ErrorBanner message={oversizedHint} /> : null}

      <DryRunToggle checked={dryRun} onChange={setDryRun} disabled={isUploading} />
      <AutoApproveToggle
        checked={autoApprove && !dryRun}
        onChange={setAutoApprove}
        disabled={isUploading || dryRun}
      />

      <div className="flex flex-wrap items-center justify-end gap-2">
        {isUploading ? (
          <Spinner label="Uploading & parsing…" size="md" className="mr-auto" />
        ) : null}
        <Button
          variant="outline"
          onClick={handleClear}
          disabled={isUploading || (!file && !result && !importMutation.error)}
        >
          Cancel
        </Button>
        <Button
          variant="gradient"
          onClick={handleUpload}
          disabled={!file || isUploading || Boolean(oversizedHint)}
        >
          <Upload className="h-4 w-4" strokeWidth={1.8} />
          {isUploading ? "Uploading…" : dryRun ? "Run preview" : "Upload"}
        </Button>
      </div>

      {errorView ? <ErrorBanner message={errorView.message} onRetry={errorView.retry} /> : null}

      {result ? <SummaryCard result={result} dryRun={dryRun} /> : null}

      {/* When nothing is loaded yet and there's no error, the drop zone is the
          empty state on its own — no extra panel needed. */}
    </div>
  );
}

export default ImportRoute;
