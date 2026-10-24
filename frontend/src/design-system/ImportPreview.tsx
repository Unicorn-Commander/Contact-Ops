/**
 * ImportPreview — auth-free showcase of the /import vCard upload page.
 *
 * Lives at /design-system/import so the page is screenshottable without a
 * Keycloak session or a running backend. Renders each variant inside a
 * "phone-like" frame (sidebar + top-bar + page body) so the layout matches
 * what the user actually sees after sign-in.
 *
 * Variants:
 *   - "empty"           drop zone idle, no file selected
 *   - "dragging"        drop zone in the dragover state (solid border + tint)
 *   - "file-dryrun"     file selected + dry-run toggle on, ready to upload
 *   - "uploading"       in-flight state with spinner + disabled buttons
 *   - "summary-success" successful upload, no errors — all four stat tiles + preview
 *   - "summary-errors"  successful upload with two parse errors, expanded
 *
 * Everything in this file is presentational — no fetch, no router calls.
 * The shapes mirror `useVcardImport.ts` field-for-field so any drift between
 * preview and real page is caught at the type level.
 */
import { useMemo, useRef } from "react";
import {
  AlertCircle,
  ArrowRight,
  Bell,
  BookUser,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileText,
  Layers,
  ListChecks,
  Menu,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  Trash2,
  Upload,
  XCircle
} from "lucide-react";
import { Spinner } from "@/design-system/Spinner";
import { cn } from "@/lib/utils";
import type { ThemeMode } from "@/design-system/tokens";

// ---- Local types (subset of useVcardImport) -------------------------------

type PreviewRecord = {
  display_name: string;
  emails: string[];
  phones: string[];
  organization?: string;
};

type PreviewError = { index: number; reason: string };

type MockResult = {
  parsed_count: number;
  proposed_count: number;
  deduped_count: number;
  skipped_count: number;
  errors: PreviewError[];
  preview: PreviewRecord[];
};

// ---- Mock data -------------------------------------------------------------

const MOCK_SUCCESS: MockResult = {
  parsed_count: 312,
  proposed_count: 287,
  deduped_count: 23,
  skipped_count: 2,
  errors: [],
  preview: [
    {
      display_name: "Alex Henderson",
      emails: ["REMOVED"],
      phones: ["REMOVED"],
      organization: "Bramble Robotics"
    },
    {
      display_name: "Mina Chen",
      emails: ["REMOVED"],
      phones: [],
      organization: "Bramble Robotics"
    },
    {
      display_name: "Rafael Ortiz",
      emails: ["REMOVED"],
      phones: ["REMOVED"],
      organization: "Northwind Labs"
    },
    {
      display_name: "Priya Shah",
      emails: [],
      phones: ["REMOVED"],
      organization: "Bramble Robotics"
    },
    {
      display_name: "Wendell Lockhart",
      emails: ["REMOVED"],
      phones: [],
      organization: "Lockhart & Associates"
    }
  ]
};

const MOCK_WITH_ERRORS: MockResult = {
  parsed_count: 184,
  proposed_count: 152,
  deduped_count: 18,
  skipped_count: 14,
  errors: [
    { index: 7, reason: "Missing FN field — vCard 4.0 requires a formatted name" },
    { index: 23, reason: "Invalid PHOTO encoding — expected base64, got binary" },
    { index: 41, reason: "EMAIL field contained 32 addresses; cap is 16" },
    { index: 58, reason: "Unterminated BEGIN:VCARD block (no matching END:VCARD)" },
    { index: 119, reason: "ORG line contained a stray semicolon — parse pointer drifted" }
  ],
  preview: [
    {
      display_name: "Robin Doe",
      emails: ["REMOVED"],
      phones: ["REMOVED"],
      organization: "Doe Holdings"
    },
    {
      display_name: "Maya Hart",
      emails: ["REMOVED"],
      phones: [],
      organization: "Bayview Health"
    },
    {
      display_name: "Sam Okoye",
      emails: ["REMOVED"],
      phones: ["REMOVED"],
      organization: "Brightwater Labs"
    },
    {
      display_name: "Ray Holloway",
      emails: ["REMOVED"],
      phones: [],
      organization: "Holloway Speaking"
    },
    {
      display_name: "",
      emails: [],
      phones: ["REMOVED"]
    }
  ]
};

// ---- Mock sub-components (lightweight twins of the real page) -------------

type Variant =
  | "empty"
  | "dragging"
  | "file-dryrun"
  | "uploading"
  | "summary-success"
  | "summary-errors";

const SAMPLE_FILENAME = "Apple Contacts Export.vcf";
const SAMPLE_SIZE = "8.4 MiB";

function MockHeader() {
  return (
    <header className="space-y-2">
      <div className="flex items-center gap-2.5">
        <span className="bg-gradient-brand flex h-9 w-9 items-center justify-center rounded-xl text-primary-foreground shadow-[var(--shadow-1)]">
          <Upload className="h-5 w-5" strokeWidth={1.8} />
        </span>
        <h1 className="text-2xl font-semibold tracking-tight">Import contacts</h1>
      </div>
      <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
        Upload a vCard (.vcf) export — from iCloud, Mac Contacts, Google, Outlook, or anywhere else. Every
        record gets <span className="font-medium text-foreground">proposed</span> to your Review Queue for
        approval.
      </p>
    </header>
  );
}

function MockDropZone({ dragging, compact }: { dragging: boolean; compact?: boolean }) {
  return (
    <div
      className={cn(
        "relative flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed bg-card/40 px-6 py-10 text-center transition-colors",
        compact && "px-4 py-8",
        dragging
          ? "border-solid border-primary bg-primary/10"
          : "border-border"
      )}
    >
      <span
        className={cn(
          "bg-gradient-brand co-glow-brand flex h-14 w-14 items-center justify-center rounded-2xl text-primary-foreground transition-transform",
          dragging && "scale-105"
        )}
        aria-hidden="true"
      >
        <Upload className="h-6 w-6" strokeWidth={1.8} />
      </span>
      <div className="space-y-1.5">
        <p className="text-base font-semibold tracking-tight text-foreground">
          {dragging ? "Drop to attach" : "Drop a .vcf file here"}
        </p>
        <p className="text-sm text-muted-foreground">
          or <span className="font-medium text-link underline underline-offset-2">click to choose</span> from your computer
        </p>
      </div>
      <p className="text-xs text-muted-foreground">Up to 50 MiB · iCloud, Mac Contacts, Google, Outlook all supported</p>
    </div>
  );
}

function MockSelectedFile({ uploading }: { uploading?: boolean }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2.5 shadow-[var(--shadow-1)]">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
        <FileText className="h-4 w-4" strokeWidth={1.8} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">{SAMPLE_FILENAME}</p>
        <p className="co-mono-numeric text-xs text-muted-foreground">{SAMPLE_SIZE}</p>
      </div>
      <span
        className={cn(
          "inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-xs font-medium",
          uploading && "opacity-60"
        )}
      >
        <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.8} />
        Replace
      </span>
      <span
        className={cn(
          "inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground",
          uploading && "opacity-60"
        )}
      >
        <Trash2 className="h-4 w-4" strokeWidth={1.8} />
      </span>
    </div>
  );
}

function MockDryRunToggle({ checked, disabled }: { checked: boolean; disabled?: boolean }) {
  return (
    <div
      className={cn(
        "group inline-flex w-full max-w-md items-center justify-between gap-3 rounded-lg border border-border bg-card p-3 text-sm transition-colors",
        checked && "border-primary/40 bg-primary/5",
        disabled && "opacity-60"
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
          <ShieldAlert className="h-3 w-3" strokeWidth={2} />
        </span>
        <span className="min-w-0 leading-snug">
          <span className="block font-medium text-foreground">Preview only (don&apos;t create proposals)</span>
          <span className="block text-xs text-muted-foreground">
            Parse the file and show the summary without writing anything to the Review Queue.
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
    </div>
  );
}

function MockActionRow({
  fileSelected,
  uploading,
  dryRun
}: {
  fileSelected: boolean;
  uploading?: boolean;
  dryRun?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {uploading ? <Spinner label="Uploading & parsing…" size="md" className="mr-auto" /> : null}
      <span
        className={cn(
          "focus-ring inline-flex h-9 items-center justify-center gap-2 whitespace-nowrap rounded-md border bg-background px-3 text-sm font-medium",
          (!fileSelected || uploading) && "opacity-50"
        )}
      >
        Cancel
      </span>
      <span
        className={cn(
          "bg-gradient-brand inline-flex h-9 items-center justify-center gap-2 whitespace-nowrap rounded-md px-3 text-sm font-medium text-primary-foreground shadow-[var(--shadow-1)]",
          (!fileSelected || uploading) && "opacity-50"
        )}
      >
        <Upload className="h-4 w-4" strokeWidth={1.8} />
        {uploading ? "Uploading…" : dryRun ? "Run preview" : "Upload"}
      </span>
    </div>
  );
}

function MockStatTile({
  label,
  value,
  icon: Icon,
  tone,
  hint
}: {
  label: string;
  value: number;
  icon: typeof Upload;
  tone: "sky" | "brand" | "emerald" | "rose" | "amber";
  hint?: string;
}) {
  const ringMap: Record<typeof tone, string> = {
    sky: "ring-[oklch(var(--co-sky-500)/0.22)] text-[oklch(var(--co-sky-500))]",
    brand: "ring-[oklch(var(--co-brand-500)/0.22)] text-primary",
    emerald: "ring-[oklch(var(--co-emerald-500)/0.22)] text-[oklch(var(--co-emerald-500))]",
    rose: "ring-[oklch(var(--co-rose-500)/0.22)] text-[oklch(var(--co-rose-500))]",
    amber: "ring-[oklch(var(--co-amber-500)/0.22)] text-[oklch(var(--co-amber-500))]"
  };
  const [ring, color] = ringMap[tone].split(" ");
  return (
    <div className={cn("rounded-xl border border-border bg-card p-5 shadow-[var(--shadow-1)] ring-1", ring)}>
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
        <Icon className={cn("h-4 w-4", color)} strokeWidth={1.8} />
      </div>
      <p className="mt-2.5 text-3xl font-semibold tabular-nums tracking-tight">{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function MockSummary({ result, dryRun }: { result: MockResult; dryRun?: boolean }) {
  return (
    <div className="space-y-5">
      <div
        className={cn(
          "rounded-xl border border-[oklch(var(--co-emerald-500)/0.35)] bg-[oklch(var(--co-emerald-500)/0.04)] text-card-foreground shadow-[var(--shadow-1)]"
        )}
      >
        <div className="flex flex-row items-center justify-between space-y-0 p-5">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-md bg-[oklch(var(--co-emerald-500)/0.18)] text-[oklch(var(--co-emerald-500))]">
              <CheckCircle2 className="h-5 w-5" strokeWidth={1.8} />
            </span>
            <div>
              <h3 className="text-base font-semibold leading-none">
                {dryRun ? "Preview complete" : "Import complete"}
              </h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {dryRun
                  ? "Nothing was written — re-upload with the toggle off to send these to the Review Queue."
                  : "Records are now in the Review Queue waiting for your approval."}
              </p>
            </div>
          </div>
          {!dryRun ? (
            <span className="hidden h-9 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-medium sm:inline-flex">
              View pending proposals
              <ArrowRight className="h-3.5 w-3.5" strokeWidth={1.8} />
            </span>
          ) : null}
        </div>
        <div className="space-y-5 p-5 pt-0">
          <div className="grid gap-4 sm:grid-cols-3 xl:grid-cols-3">
            <MockStatTile label="Parsed" value={result.parsed_count} icon={FileText} tone="sky" hint="records read from file" />
            <MockStatTile
              label="Proposed"
              value={result.proposed_count}
              icon={Sparkles}
              tone="brand"
              hint={dryRun ? "would be created" : "queued for review"}
            />
            <MockStatTile
              label="Deduped"
              value={result.deduped_count}
              icon={Layers}
              tone="emerald"
              hint="matched existing people"
            />
            <MockStatTile
              label="Skipped"
              value={result.skipped_count}
              icon={XCircle}
              tone={result.skipped_count > 0 ? "rose" : "amber"}
              hint={result.skipped_count > 0 ? "see errors below" : "none"}
            />
          </div>

          <div className="rounded-lg border border-border bg-card">
            <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Preview · first {result.preview.length} of {result.preview.length}
              </p>
            </div>
            <ul className="divide-y divide-border">
              {result.preview.map((record, idx) => (
                <li
                  key={`${record.display_name}-${idx}`}
                  className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5"
                >
                  <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                    {record.display_name || (
                      <span className="italic text-muted-foreground">No display name</span>
                    )}
                  </p>
                  {record.organization ? (
                    <p className="truncate text-xs text-muted-foreground">{record.organization}</p>
                  ) : null}
                  {record.emails[0] ? (
                    <p className="truncate font-mono text-xs text-muted-foreground">{record.emails[0]}</p>
                  ) : (
                    <p className="text-xs italic text-muted-foreground">no email</p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

function MockErrorsCollapsible({ result, expanded }: { result: MockResult; expanded?: boolean }) {
  if (result.errors.length === 0) return null;
  return (
    <div className="rounded-xl border border-[oklch(var(--co-rose-500)/0.35)] bg-card text-card-foreground shadow-[var(--shadow-1)]">
      <div className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-md bg-[oklch(var(--co-rose-500)/0.15)] text-[oklch(var(--co-rose-500))]">
            <AlertCircle className="h-5 w-5" strokeWidth={1.8} />
          </span>
          <div>
            <p className="text-sm font-semibold leading-tight">
              {result.errors.length} records could not be parsed
            </p>
            <p className="text-xs text-muted-foreground">
              Click to {expanded ? "hide" : "view"} the parse errors
            </p>
          </div>
        </div>
        {expanded ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
        )}
      </div>
      {expanded ? (
        <div className="p-5 pt-0">
          <ul className="co-scrollbar max-h-72 space-y-1.5 overflow-y-auto rounded-md border border-border bg-background/60 p-3 font-mono text-xs">
            {result.errors.map((err) => (
              <li key={err.index} className="flex gap-3">
                <span className="co-mono-numeric shrink-0 text-muted-foreground">#{err.index}</span>
                <span className="break-words text-foreground/90">{err.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

// ---- Outer frame (mimics AppShell sidebar + topbar) ----------------------

function MockTopBar() {
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
      <span className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground" aria-hidden="true">
        <Bell className="h-4 w-4" strokeWidth={1.8} />
      </span>
    </header>
  );
}

function MockSidebar({ compact }: { compact?: boolean }) {
  if (compact) return null;
  const items: { label: string; icon: typeof Upload; active?: boolean; accent: string }[] = [
    { label: "Dashboard", icon: ListChecks, accent: "text-[oklch(var(--co-brand-500))]" },
    { label: "People", icon: ListChecks, accent: "text-[oklch(var(--co-sky-500))]" },
    { label: "Organizations", icon: ListChecks, accent: "text-[oklch(var(--co-emerald-500))]" },
    { label: "Tags", icon: ListChecks, accent: "text-[oklch(var(--co-amber-500))]" },
    { label: "Review Queue", icon: ListChecks, accent: "text-[oklch(var(--co-rose-500))]" },
    { label: "Import", icon: Upload, active: true, accent: "text-[oklch(var(--co-emerald-500))]" }
  ];
  return (
    <aside className="hidden w-44 shrink-0 border-r border-border bg-[oklch(var(--sidebar))] lg:block">
      <div className="flex h-14 items-center gap-2 border-b border-border px-3">
        <span className="bg-gradient-brand flex h-7 w-7 items-center justify-center rounded-lg text-primary-foreground">
          <BookUser className="h-3.5 w-3.5" strokeWidth={1.8} />
        </span>
        <span className="text-xs font-semibold">Contact-Ops</span>
      </div>
      <nav className="space-y-1 p-2">
        {items.map((item) => (
          <span
            key={item.label}
            className={cn(
              "flex h-8 items-center gap-2 rounded-md border-l-2 px-2 text-[11px] font-medium",
              item.active
                ? "border-primary bg-primary/10 text-foreground"
                : "border-transparent text-muted-foreground"
            )}
          >
            <item.icon className={cn("h-3.5 w-3.5", item.accent)} strokeWidth={1.8} />
            {item.label}
          </span>
        ))}
      </nav>
    </aside>
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
  const result =
    variant === "summary-errors"
      ? MOCK_WITH_ERRORS
      : variant === "summary-success"
        ? MOCK_SUCCESS
        : null;
  const fileSelected =
    variant === "file-dryrun" ||
    variant === "uploading" ||
    variant === "summary-success" ||
    variant === "summary-errors";
  const dryRun = variant === "file-dryrun";
  const isUploading = variant === "uploading";

  return (
    <div
      data-theme={theme}
      style={{ width }}
      className={cn(
        "relative isolate overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background text-foreground shadow-[var(--shadow-2)]"
      )}
    >
      <div className="flex">
        <MockSidebar compact={compact} />
        <div className="min-w-0 flex-1">
          <MockTopBar />
          <div className={cn("mx-auto max-w-3xl space-y-6 p-4 lg:p-6", compact && "p-3")}>
            <MockHeader />
            <div className="space-y-3">
              <MockDropZone dragging={variant === "dragging"} compact={compact} />
              {fileSelected ? <MockSelectedFile uploading={isUploading} /> : null}
            </div>
            <MockDryRunToggle checked={dryRun} disabled={isUploading} />
            <MockActionRow fileSelected={fileSelected} uploading={isUploading} dryRun={dryRun} />
            {result ? <MockSummary result={result} dryRun={dryRun} /> : null}
            {variant === "summary-errors" ? (
              <MockErrorsCollapsible result={MOCK_WITH_ERRORS} expanded />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---- Public showcase entry ------------------------------------------------

export function ImportPreview() {
  const previews = useMemo(
    () => [
      { theme: "dark" as ThemeMode, variant: "empty" as Variant, title: "Empty · drop zone idle (dark)" },
      { theme: "light" as ThemeMode, variant: "empty" as Variant, title: "Empty · drop zone idle (light)" },
      { theme: "dark" as ThemeMode, variant: "dragging" as Variant, title: "Dragging · dragover state (dark)" },
      { theme: "light" as ThemeMode, variant: "dragging" as Variant, title: "Dragging · dragover state (light)" },
      { theme: "dark" as ThemeMode, variant: "file-dryrun" as Variant, title: "File selected · dry-run on (dark)" },
      { theme: "light" as ThemeMode, variant: "file-dryrun" as Variant, title: "File selected · dry-run on (light)" },
      { theme: "dark" as ThemeMode, variant: "uploading" as Variant, title: "Uploading · in-flight (dark)" },
      { theme: "light" as ThemeMode, variant: "uploading" as Variant, title: "Uploading · in-flight (light)" },
      { theme: "dark" as ThemeMode, variant: "summary-success" as Variant, title: "Summary · success (dark)" },
      { theme: "light" as ThemeMode, variant: "summary-success" as Variant, title: "Summary · success (light)" },
      { theme: "dark" as ThemeMode, variant: "summary-errors" as Variant, title: "Summary · errors expanded (dark)" },
      { theme: "light" as ThemeMode, variant: "summary-errors" as Variant, title: "Summary · errors expanded (light)" }
    ],
    []
  );

  const containerRef = useRef<HTMLDivElement | null>(null);

  return (
    <section ref={containerRef} className="space-y-6">
      <div>
        <h2 className="text-base font-semibold">vCard Import</h2>
        <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
          The /import page — drag-drop a .vcf, toggle dry-run, upload. Backend parses + dedups; every record is{" "}
          <span className="font-medium text-foreground">proposed</span> to the Review Queue. Six states across both
          themes plus the mobile frame at the bottom.
        </p>
      </div>

      <div className="grid gap-6 2xl:grid-cols-2">
        {previews.map(({ theme, variant, title }) => (
          <div key={`${theme}-${variant}`} className="space-y-2">
            <h3 className="text-sm font-medium">{title}</h3>
            <Frame theme={theme} variant={variant} />
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Mobile (375px) · empty (dark)</h3>
        <div className="mx-auto w-[375px] max-w-full">
          <Frame theme="dark" variant="empty" width={375} compact />
        </div>
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Mobile (375px) · summary success (light)</h3>
        <div className="mx-auto w-[375px] max-w-full">
          <Frame theme="light" variant="summary-success" width={375} compact />
        </div>
      </div>
    </section>
  );
}

export default ImportPreview;
