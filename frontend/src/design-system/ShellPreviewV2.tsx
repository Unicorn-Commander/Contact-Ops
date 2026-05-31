/**
 * ShellPreviewV2 — auth-free, backend-free preview of the Design Language v2
 * app-shell chrome (DEV-ONLY). Route: /design-system/shell-v2.
 *
 * Phase 3 restyles three pieces of the live shell: the first-class "Ask AI ⌘J"
 * pill (top bar), the docked floating "Ask the Analyst" launcher (bottom-right),
 * and the v2 glass tenant-switcher chip. The real AppShell depends on
 * react-oidc-context + live MCP data, so this page mirrors its header markup
 * EXACTLY (presentational twin) with static data so the orchestrator can
 * screenshot the new chrome without a Keycloak session.
 *
 * It mounts the REAL <AskAnalystDock> (it takes a plain onOpen handler, so it
 * needs no auth) and reuses the real <DashboardPreviewBody> for the page body,
 * so the launcher + body exercise the same code paths the live app does.
 *
 * Keep the header recipe here in sync with components/AppShell.tsx — this is a
 * presentational twin, not the source of truth.
 *
 * Dark/light: this page owns its own theme toggle (button + the `t` key) and
 * applies it both to a scoped data-theme wrapper AND through the design-system
 * helper, so the v2 glass tokens resolve correctly for the screenshot.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Bell,
  BookUser,
  Building2,
  Command,
  Home,
  ListChecks,
  LogOut,
  Menu,
  Moon,
  Network,
  Search,
  Settings,
  Sparkles,
  Sun,
  Tags,
  Users
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AskAnalystDock } from "@/components/AskAnalystDock";
import { KeyboardHint } from "@/design-system/KeyboardHint";
import { DashboardPreviewBody } from "@/design-system/ShellPreview";
import { applyThemePreference, getThemePreference, type ThemeMode } from "@/design-system/tokens";
import { cn } from "@/lib/utils";

const nav = [
  { label: "Dashboard", icon: Home, active: true, accent: "text-[oklch(var(--co-brand-500))]" },
  { label: "People", icon: Users, accent: "text-[oklch(var(--co-sky-500))]" },
  { label: "Organizations", icon: Building2, accent: "text-[oklch(var(--co-emerald-500))]" },
  { label: "Tags", icon: Tags, accent: "text-[oklch(var(--co-amber-500))]" },
  { label: "Graph", icon: Network, accent: "text-[oklch(var(--co-fuchsia-500))]" },
  { label: "Review Queue", icon: ListChecks, accent: "text-[oklch(var(--co-rose-500))]" }
] as const;

const adminNav = [
  { label: "Tenants", icon: Building2, accent: "text-[oklch(var(--co-sky-500))]" },
  { label: "Settings", icon: Settings, accent: "text-muted-foreground" }
] as const;

function NavItem({ label, icon: Icon, active, accent }: { label: string; icon: LucideIcon; active?: boolean; accent: string }) {
  return (
    <span
      className={cn(
        "flex items-center gap-3 rounded-lg border-l-2 border-transparent px-3 py-2 text-sm font-medium",
        active ? "border-primary bg-primary/10 text-foreground" : "text-muted-foreground"
      )}
    >
      <Icon className={cn("h-[18px] w-[18px] shrink-0", accent)} strokeWidth={1.8} />
      {label}
    </span>
  );
}

/**
 * Static v2 tenant chip — the NON-INTERACTIVE single-workspace case (the honest
 * default for a user with one tenant). Mirrors the markup TenantSwitcher renders
 * for `!multi`. The multi-tenant dropdown variant is exercised in the live app;
 * here we show the calm chip so the v2 glass treatment is screenshottable.
 */
function TenantChipPreview() {
  return (
    <span
      className="co-v2-glass-calm flex h-9 cursor-default items-center gap-2 rounded-full pl-1.5 pr-3 text-sm font-medium text-foreground"
      aria-label="Workspace: Magic Unicorn"
    >
      <span className="bg-gradient-brand flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-primary-foreground">
        <Building2 className="h-3.5 w-3.5" strokeWidth={1.8} />
      </span>
      <span className="hidden max-w-[160px] truncate md:inline">Magic Unicorn</span>
    </span>
  );
}

/** The v2 "Ask AI ⌘J" pill — presentational twin of the live AppShell trigger. */
function AskAiPillPreview({ onOpen, expanded }: { onOpen: () => void; expanded: boolean }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      title="Ask AI — open the AI Contacts Analyst (⌘J)"
      aria-label="Ask AI. Open the AI Contacts Analyst."
      aria-keyshortcuts="Meta+J Control+J"
      aria-expanded={expanded}
      className="co-v2-hover-bloom co-v2-glow-strong focus-ring bg-gradient-brand inline-flex h-9 items-center gap-2 rounded-full px-2.5 text-sm font-semibold text-primary-foreground sm:px-3.5"
    >
      <Sparkles className="h-4 w-4 shrink-0" strokeWidth={2} />
      <span className="hidden sm:inline">Ask AI</span>
      <kbd className="ml-0.5 hidden items-center gap-0.5 rounded bg-black/20 px-1.5 py-0.5 font-mono text-[11px] font-medium text-primary-foreground/90 sm:inline-flex">
        <Command className="h-3 w-3" strokeWidth={2} />J
      </kbd>
    </button>
  );
}

export function ShellPreviewV2() {
  const [theme, setTheme] = useState<ThemeMode>(() => {
    const requested = new URLSearchParams(window.location.search).get("theme");
    return requested === "light" || requested === "dark" ? requested : getThemePreference();
  });
  // Stand in for the shell's analyst-open state so the dock hides on "open",
  // exactly like the live shell hides it while the panel is mounted.
  const [analystOpen, setAnalystOpen] = useState(false);

  useEffect(() => {
    applyThemePreference(theme);
  }, [theme]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (!typing && event.key.toLowerCase() === "t" && !event.metaKey && !event.ctrlKey && !event.altKey) {
        setTheme((value) => (value === "dark" ? "light" : "dark"));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const nextTheme = theme === "dark" ? "light" : "dark";
  const reduced = useMemo(
    () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    []
  );

  return (
    <div data-theme={theme} className="relative min-h-screen bg-background text-foreground">
      {/* Preview chrome banner */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card/60 px-4 py-2.5">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">Contact-Ops</span>
          <span className="text-sm font-semibold">
            Shell v2 preview — Ask AI pill · docked launcher · v2 tenant chip
          </span>
        </div>
        <div className="flex items-center gap-2">
          {reduced ? (
            <span className="rounded-full border border-border bg-muted px-2.5 py-1 text-[11px] text-muted-foreground">
              reduced-motion: static
            </span>
          ) : null}
          <span className="hidden items-center gap-1 rounded-md border border-border bg-muted px-1.5 py-1 font-mono text-[11px] text-muted-foreground sm:inline-flex">
            T
          </span>
          <Button variant="outline" size="sm" onClick={() => setTheme(nextTheme)} aria-label={`Switch to ${nextTheme} mode`}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            <span className="capitalize">{nextTheme}</span>
          </Button>
        </div>
      </div>

      {/* Shell twin: sidebar + sticky v2 top bar + dashboard body */}
      <div className="flex min-h-[760px]">
        <aside className="hidden w-64 shrink-0 border-r bg-[oklch(var(--sidebar))] lg:flex lg:flex-col">
          <div className="flex h-16 items-center border-b px-4">
            <span className="flex items-center gap-3">
              <span className="bg-gradient-brand flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-primary-foreground shadow-[var(--shadow-1)]">
                <BookUser className="h-5 w-5" strokeWidth={1.8} />
              </span>
              <span className="leading-tight">
                <span className="block text-base font-semibold tracking-tight">Contact-Ops</span>
                <span className="block text-xs text-muted-foreground">Identity hub</span>
              </span>
            </span>
          </div>
          <nav className="flex-1 space-y-1 overflow-y-auto p-3">
            {nav.map((item) => (
              <NavItem key={item.label} {...item} />
            ))}
            <div className="px-3 pb-1.5 pt-4">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Admin</p>
            </div>
            {adminNav.map((item) => (
              <NavItem key={item.label} {...item} />
            ))}
          </nav>
          <div className="border-t p-3">
            <div className="flex items-center gap-3 px-1 py-2">
              <span className="co-human-sigil flex h-9 w-9 shrink-0 items-center justify-center text-sm">AS</span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">Aaron Stransky</p>
                <span className="mt-0.5 inline-flex items-center rounded-full bg-primary/10 px-1.5 py-0.5 text-[11px] font-medium text-primary">
                  Admin
                </span>
              </div>
            </div>
            <Button variant="ghost" className="mt-1 w-full justify-start gap-2 text-muted-foreground">
              <LogOut className="h-4 w-4" strokeWidth={1.8} />
              Sign out
            </Button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="co-glass sticky top-0 z-30 flex h-16 items-center gap-3 border-b px-4 lg:px-6">
            <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation">
              <Menu className="h-5 w-5" strokeWidth={1.8} />
            </Button>

            {/* C — v2 tenant chip (honest single-workspace, non-interactive). */}
            <TenantChipPreview />

            {/* Search entry (opens ⌘K palette in the live app). */}
            <button
              type="button"
              aria-label="Search people, organizations, and tags. Opens the command palette."
              className="focus-ring relative ml-auto hidden h-9 max-w-sm flex-1 items-center gap-2 rounded-md border bg-background/60 pl-9 pr-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted md:flex"
            >
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                strokeWidth={1.8}
              />
              <span className="flex-1 truncate">Search people, orgs, tags…</span>
              <KeyboardHint keys="mod+K" label="Open command palette" className="hidden lg:inline-flex" />
            </button>

            <div className="ml-auto flex items-center gap-1 md:ml-0">
              <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open command palette">
                <Search className="h-4 w-4" strokeWidth={1.8} />
              </Button>

              {/* A — first-class "Ask AI ⌘J" pill (opens the Analyst → here, the dock-hiding toggle). */}
              <AskAiPillPreview onOpen={() => setAnalystOpen((v) => !v)} expanded={analystOpen} />

              <Button variant="ghost" size="icon" aria-label="Notifications">
                <Bell className="h-4 w-4" strokeWidth={1.8} />
              </Button>
              <Button variant="ghost" size="icon" aria-label="Toggle color mode" onClick={() => setTheme(nextTheme)}>
                {theme === "dark" ? <Sun className="h-4 w-4" strokeWidth={1.8} /> : <Moon className="h-4 w-4" strokeWidth={1.8} />}
              </Button>
              <Button variant="outline" size="icon" className="h-9 w-9" aria-label="Account">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-xs font-medium text-primary-foreground">
                  A
                </span>
              </Button>
            </div>
          </header>

          <div className="co-scrollbar flex-1 overflow-y-auto p-4 lg:p-6">
            {analystOpen ? (
              <div className="mb-4 rounded-[var(--radius-md)] border border-[oklch(var(--co-brand-300)/0.4)] bg-[oklch(var(--co-brand-500)/0.08)] px-3 py-2 text-sm text-foreground">
                Analyst panel “open” (preview) — the docked launcher is hidden while open, mirroring the live shell.
                Toggle the Ask AI pill again to bring the launcher back.
              </div>
            ) : null}
            <DashboardPreviewBody />
          </div>
        </div>
      </div>

      {/* B — the REAL docked launcher. Hidden while the (preview) panel is open. */}
      <AskAnalystDock onOpen={() => setAnalystOpen(true)} hidden={analystOpen} />
    </div>
  );
}

export default ShellPreviewV2;
