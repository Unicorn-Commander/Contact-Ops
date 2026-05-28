/**
 * Auth-free, backend-free previews of the AppShell + Dashboard for the
 * /design-system showcase. The real components depend on react-oidc-context and
 * live MCP data; these mirror their exact markup with static demo data so the
 * canonical shell + command-center dashboard are screenshottable without login.
 *
 * Keep the class recipes here in sync with components/AppShell.tsx and
 * routes/pages/Dashboard.tsx — these are presentational twins, not the source
 * of truth.
 */
import type { ReactNode } from "react";
import {
  Activity,
  Bell,
  Building2,
  BookUser,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  GitMerge,
  Home,
  ListChecks,
  LogOut,
  Menu,
  Moon,
  Network,
  Plug,
  Plus,
  Search,
  Settings,
  Sparkles,
  Tags,
  UserPlus,
  Upload,
  Users
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ThemeMode } from "@/design-system/tokens";
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

type StatTone = "fuchsia" | "sky" | "amber" | "emerald";
const toneClasses: Record<StatTone, { tile: string; icon: string }> = {
  fuchsia: { tile: "ring-[oklch(var(--co-brand-500)/0.22)]", icon: "text-primary" },
  sky: { tile: "ring-[oklch(var(--co-sky-500)/0.22)]", icon: "text-[oklch(var(--co-sky-500))]" },
  amber: { tile: "ring-[oklch(var(--co-amber-500)/0.22)]", icon: "text-[oklch(var(--co-amber-500))]" },
  emerald: { tile: "ring-[oklch(var(--co-emerald-500)/0.22)]", icon: "text-[oklch(var(--co-emerald-500))]" }
};

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

function Stat({
  label,
  value,
  icon: Icon,
  tone,
  hint,
  drill
}: {
  label: string;
  value: string | number;
  icon: LucideIcon;
  tone: StatTone;
  hint?: string;
  drill?: boolean;
}) {
  const t = toneClasses[tone];
  return (
    <div className="relative">
      <Card className={cn("h-full ring-1", t.tile)}>
        <CardContent className="p-5">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
            <Icon className={cn("h-4 w-4", t.icon)} strokeWidth={1.8} />
          </div>
          <p className="mt-2.5 text-3xl font-semibold tabular-nums tracking-tight">{value}</p>
          {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
        </CardContent>
      </Card>
      {drill ? (
        <ChevronRight className="absolute bottom-5 right-5 h-4 w-4 text-muted-foreground" strokeWidth={1.8} aria-hidden />
      ) : null}
    </div>
  );
}

function QuickAction({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <Button variant="outline" className="w-full justify-start gap-2.5">
      <Icon className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
      {children}
    </Button>
  );
}

function StatusRow({ label, detail, ok }: { label: string; detail: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2 text-sm">
      <span className="inline-flex items-center gap-2">
        <CheckCircle2
          className={cn("h-4 w-4", ok ? "text-[oklch(var(--co-emerald-500))]" : "text-muted-foreground")}
          strokeWidth={1.8}
        />
        <span className="text-foreground">{label}</span>
      </span>
      <span className="truncate text-xs text-muted-foreground">{detail}</span>
    </div>
  );
}

const demoProposals = [
  { name: "Aaron Stransky", kind: "person" as const, action: "merge duplicate", agent: "dedupe-agent", conf: 96 },
  { name: "Bayview Health", kind: "org" as const, action: "update domain", agent: "enrichment-agent", conf: 88 },
  { name: "Shafen Khan", kind: "person" as const, action: "add phone", agent: "carddav-reconcile", conf: 74 },
  { name: "Magic Unicorn LLC", kind: "org" as const, action: "set industry", agent: "enrichment-agent", conf: 81 }
];

const demoActivity = [
  { actor: "Aaron", verb: "Approved", what: "merge · Jane Doe", when: "2m ago" },
  { actor: "enrichment-agent", verb: "Auto-applied", what: "domain · acme.com", when: "14m ago", agent: true },
  { actor: "Aaron", verb: "Edited", what: "headline · Rocky Doe", when: "1h ago" },
  { actor: "tag-agent", verb: "Proposed", what: "tag · vendor", when: "3h ago", agent: true }
];

/**
 * The Dashboard command-center body, with static demo data. `stack` forces the
 * single-column mobile layout (the embedded preview can't rely on viewport
 * breakpoints, which key off the real window width, not the 375px frame).
 */
export function DashboardPreviewBody({ stack = false }: { stack?: boolean }) {
  const statGrid = stack ? "grid grid-cols-1 gap-4" : "grid gap-4 md:grid-cols-2 xl:grid-cols-4";
  const mainGrid = stack ? "grid gap-6" : "grid gap-6 lg:grid-cols-3";
  const mainCol = stack ? "space-y-6" : "space-y-6 lg:col-span-2";
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Good afternoon, <span className="bg-gradient-brand-text">Aaron</span>
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Your canonical identity hub at a glance · 41 MCP tools available.
          </p>
        </div>
        <Button variant="gradient" className="h-9">
          <Plus className="h-4 w-4" strokeWidth={1.8} />
          Add person
        </Button>
      </div>

      <div className="relative max-w-2xl">
        <Search
          className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          strokeWidth={1.8}
        />
        <input
          readOnly
          aria-label="Search people, organizations, and tags"
          placeholder="Search people, orgs, tags…"
          className="focus-ring h-11 w-full rounded-md border bg-card pl-11 pr-4 text-sm placeholder:text-muted-foreground"
        />
      </div>

      <div className={statGrid}>
        <Stat label="People" value={1284} icon={Users} tone="fuchsia" drill />
        <Stat label="Organizations" value={206} icon={Building2} tone="emerald" drill />
        <Stat label="Pending reviews" value={4} icon={ListChecks} tone="amber" hint="Awaiting your decision" drill />
        <Stat label="Agent activity" value={3} icon={Sparkles} tone="sky" hint="agents proposing changes" />
      </div>

      <div className={mainGrid}>
        <div className={mainCol}>
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <CardTitle className="flex items-center gap-2 text-base">
                <ListChecks className="h-4 w-4 text-[oklch(var(--co-amber-500))]" strokeWidth={1.8} />
                Pending proposals
              </CardTitle>
              <Button variant="ghost" size="sm" className="text-link">
                Review all
                <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.8} />
              </Button>
            </CardHeader>
            <CardContent>
              <div className="divide-y divide-border">
                {demoProposals.map((p) => (
                  <div key={p.name} className="-mx-2 flex items-center gap-3 rounded-md px-2 py-2.5">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                      {p.kind === "org" ? (
                        <Building2 className="h-4 w-4" strokeWidth={1.8} />
                      ) : (
                        <Users className="h-4 w-4" strokeWidth={1.8} />
                      )}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{p.name}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        <span className="capitalize">{p.action}</span>
                        {" · "}
                        <span className="font-mono">{p.agent}</span>
                      </span>
                    </span>
                    <span className="co-mono-numeric shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                      {p.conf}%
                    </span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Activity className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
                Recent activity
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="divide-y divide-border">
                {demoActivity.map((a, i) => (
                  <div key={i} className="flex items-center justify-between gap-2 py-3 text-sm">
                    <span className="inline-flex min-w-0 items-center gap-2">
                      <span
                        className={cn(
                          "inline-flex h-5 shrink-0 items-center rounded px-1.5 text-[11px] font-medium",
                          a.agent
                            ? "co-agent-indicator border font-mono text-[oklch(var(--co-agent-color))]"
                            : "co-human-indicator border"
                        )}
                      >
                        {a.actor}
                      </span>
                      <span className="truncate text-muted-foreground">
                        <span className="text-foreground">{a.verb}</span> {a.what}
                      </span>
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">{a.when}</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Quick actions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <QuickAction icon={UserPlus}>Add person</QuickAction>
              <QuickAction icon={Building2}>New organization</QuickAction>
              <QuickAction icon={ListChecks}>Review proposals</QuickAction>
              <QuickAction icon={Upload}>Import contacts</QuickAction>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Plug className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
                Sync &amp; integrations
              </CardTitle>
            </CardHeader>
            <CardContent className="divide-y divide-border py-0">
              <StatusRow label="MCP surface" detail="41 tools" ok />
              <StatusRow label="CardDAV sync" detail="Connected" ok />
              <StatusRow label="Agent pipeline" detail="4 pending" ok />
            </CardContent>
          </Card>

          <Card>
            <CardContent className="flex items-start gap-3 p-5">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                <GitMerge className="h-4 w-4" strokeWidth={1.8} />
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium">Recent merges</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Duplicate resolutions appear here as agents reconcile records. None in the last 30 days.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

/**
 * Full shell preview: sidebar + sticky top bar + the dashboard body. Rendered at
 * a fixed height so it screenshots as a self-contained command center. `compact`
 * collapses the sidebar to mimic the 375px mobile layout.
 */
export function ShellPreview({ theme, compact = false }: { theme: ThemeMode; compact?: boolean }) {
  return (
    <div
      data-theme={theme}
      className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background text-foreground shadow-[var(--shadow-2)]"
    >
      <div className="flex h-[760px]">
        {/* Sidebar (hidden in compact/mobile preview) */}
        {!compact ? (
          <aside className="hidden w-64 shrink-0 border-r bg-[oklch(var(--sidebar))] sm:flex sm:flex-col">
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
        ) : null}

        {/* Main column */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="co-glass sticky top-0 z-10 flex h-16 items-center gap-3 border-b px-4">
            {compact ? (
              <Button variant="ghost" size="icon" aria-label="Open navigation">
                <Menu className="h-5 w-5" strokeWidth={1.8} />
              </Button>
            ) : null}
            <Button variant="outline" className="h-9 gap-2 px-2.5">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary/10 text-primary">
                <Building2 className="h-3.5 w-3.5" strokeWidth={1.8} />
              </span>
              <span className="hidden max-w-[160px] truncate text-sm font-medium md:inline">Personal</span>
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={1.8} />
            </Button>
            {!compact ? (
              <div className="relative ml-auto hidden max-w-sm flex-1 md:block">
                <Search
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                  strokeWidth={1.8}
                />
                <input
                  readOnly
                  placeholder="Search people, orgs, tags…"
                  className="focus-ring h-9 w-full rounded-md border bg-background/60 pl-9 pr-3 text-sm placeholder:text-muted-foreground"
                />
              </div>
            ) : null}
            <div className="ml-auto flex items-center gap-1 md:ml-0">
              <Button variant="ghost" size="icon" aria-label="Notifications">
                <Bell className="h-4 w-4" strokeWidth={1.8} />
              </Button>
              <Button variant="ghost" size="icon" aria-label="Toggle color mode">
                <Moon className="h-4 w-4" strokeWidth={1.8} />
              </Button>
              <Button variant="outline" size="icon" className="h-9 w-9" aria-label="Account">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-xs font-medium text-primary-foreground">
                  A
                </span>
              </Button>
            </div>
          </header>
          <div className="co-scrollbar flex-1 overflow-y-auto p-4 lg:p-6">
            <DashboardPreviewBody stack={compact} />
          </div>
        </div>
      </div>
    </div>
  );
}
