/**
 * Auth-free, backend-free preview of the global ⌘K command palette for the
 * /design-system showcase. The real <CommandPalette> depends on
 * react-oidc-context (access token) + live MCP search and renders through a
 * Radix portal; this twin mirrors its exact markup with static demo data so the
 * palette is screenshottable (dark + light) without login or a portal.
 *
 * Keep the class recipes here in sync with components/CommandPalette.tsx —
 * this is a presentational twin, not the source of truth.
 */
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
import type { ThemeMode } from "@/design-system/tokens";
import { cn } from "@/lib/utils";

type Kind = "person" | "org" | "tag";

const KIND_ICON: Record<Kind, LucideIcon> = {
  person: Users,
  org: Building2,
  tag: TagsIcon
};

const KIND_ACCENT: Record<Kind, string> = {
  person: "text-[oklch(var(--co-sky-500))]",
  org: "text-[oklch(var(--co-emerald-500))]",
  tag: "text-[oklch(var(--co-amber-500))]"
};

type Row = { kind: Kind; label: string; sublabel?: string; meta?: string };

function ResultRow({ row, selected }: { row: Row; selected?: boolean }) {
  const Icon = KIND_ICON[row.kind];
  return (
    <div
      aria-selected={selected || undefined}
      className={cn(
        "flex items-center gap-3 rounded-[var(--radius-sm)] px-2 py-2 text-sm text-foreground",
        selected && "bg-primary/12"
      )}
    >
      <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted", KIND_ACCENT[row.kind])}>
        <Icon className="h-4 w-4" strokeWidth={1.8} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{row.label}</span>
        {row.sublabel ? <span className="block truncate text-xs text-muted-foreground">{row.sublabel}</span> : null}
      </span>
      {row.meta ? <span className="shrink-0 text-xs capitalize text-muted-foreground">{row.meta}</span> : null}
    </div>
  );
}

function ActionRow({ icon: Icon, label }: { icon: LucideIcon; label: string }) {
  return (
    <div className="flex items-center gap-3 rounded-[var(--radius-sm)] px-2 py-2 text-sm text-foreground">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
        <Icon className="h-4 w-4" strokeWidth={1.8} />
      </span>
      <span className="flex-1 truncate font-medium">{label}</span>
      <Plus className="h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.8} aria-hidden />
    </div>
  );
}

function GroupHeading({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{children}</p>
  );
}

function Keycap({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex min-w-[1.25rem] items-center justify-center rounded-[var(--radius-sm)] border border-border bg-card px-1 py-0.5 font-mono text-[10px] font-medium leading-none text-muted-foreground">
      {children}
    </kbd>
  );
}

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

/** The palette panel itself (input + list + footer), no portal/overlay. */
function PalettePanel({ mode }: { mode: "search" | "empty" }) {
  return (
    <div className="overflow-hidden rounded-[var(--radius-lg)] border bg-popover text-popover-foreground shadow-[var(--shadow-4)]">
      <div className="flex items-center gap-2.5 border-b px-3.5">
        <Search className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={1.8} aria-hidden />
        {mode === "search" ? (
          <span className="flex h-12 w-full items-center text-sm">
            shaf
            <span className="ml-px inline-block h-4 w-px bg-foreground/70" aria-hidden />
          </span>
        ) : (
          <span className="flex h-12 w-full items-center text-sm text-muted-foreground">Search people, orgs, tags…</span>
        )}
      </div>

      <div className="co-scrollbar max-h-[420px] overflow-y-auto p-2">
        {mode === "search" ? (
          <>
            <GroupHeading>People</GroupHeading>
            <ResultRow row={{ kind: "person", label: "Sam Okoye", sublabel: "Cofounder · Brightwater Labs" }} selected />
            <ResultRow row={{ kind: "person", label: "Maya Hart", sublabel: "Partner · Bayview Health" }} />
            <GroupHeading>Organizations</GroupHeading>
            <ResultRow row={{ kind: "org", label: "Brightwater Labs", sublabel: "Healthcare IT" }} />
            <GroupHeading>Quick actions</GroupHeading>
            <ActionRow icon={UserPlus} label="Add person" />
          </>
        ) : (
          <>
            <GroupHeading>Favorites</GroupHeading>
            <ResultRow row={{ kind: "person", label: "Aaron Stransky", meta: "person" }} selected />
            <ResultRow row={{ kind: "org", label: "Magic Unicorn LLC", meta: "org" }} />
            <GroupHeading>Recent</GroupHeading>
            <ResultRow row={{ kind: "person", label: "Robin Doe", meta: "person" }} />
            <ResultRow row={{ kind: "org", label: "Bayview Health", meta: "org" }} />
            <ResultRow row={{ kind: "tag", label: "vendor", sublabel: "18 tagged", meta: "tag" }} />
            <GroupHeading>Quick actions</GroupHeading>
            <ActionRow icon={UserPlus} label="Add person" />
            <ActionRow icon={Building2} label="New organization" />
            <ActionRow icon={Upload} label="Import contacts" />
            <ActionRow icon={ListChecks} label="Open Review Queue" />
          </>
        )}
      </div>

      <PaletteFooter />
    </div>
  );
}

/**
 * Full preview: the palette floating over a dimmed app backdrop, exactly as it
 * appears when opened. `mode` toggles the dual behaviour:
 *   - "empty"  → Favorites + Recent + Quick actions (no query)
 *   - "search" → grouped People / Organizations / Tags + matching Quick actions
 */
export function CommandPalettePreview({
  theme,
  mode = "empty"
}: {
  theme: ThemeMode;
  mode?: "search" | "empty";
}) {
  return (
    <div
      data-theme={theme}
      className="relative overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background text-foreground shadow-[var(--shadow-2)]"
    >
      {/* Dimmed app backdrop (mirrors the Radix overlay) */}
      <div className="absolute inset-0 bg-background/70 backdrop-blur-sm" aria-hidden />
      <div className="relative flex min-h-[520px] items-start justify-center px-4 pb-10 pt-[12%]">
        <div className="w-full max-w-xl">
          <PalettePanel mode={mode} />
        </div>
      </div>
    </div>
  );
}
