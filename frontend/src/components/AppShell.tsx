import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import {
  Bell,
  BookUser,
  Building2,
  Home,
  ListChecks,
  LogOut,
  Menu,
  Moon,
  Network,
  Search,
  Settings,
  Tags,
  Sun,
  Users
} from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { useAuth } from "react-oidc-context";
import { useHotkeys } from "react-hotkeys-hook";
import { Button } from "@/components/ui/button";
import { KeyboardHint } from "@/design-system/KeyboardHint";
import { TenantSwitcher } from "@/components/domain/TenantSwitcher";
import { cn, initials } from "@/lib/utils";
import { UserMenu } from "@/components/UserMenu";

// Lazy-load the palette so its cmdk + Radix-dialog code splits into the async
// `cmdk` chunk (matching the Inbox) instead of weighing down the eager shell
// bundle. It only loads on the first ⌘K / search-button press.
const CommandPalette = lazy(() =>
  import("@/components/CommandPalette").then((m) => ({ default: m.CommandPalette }))
);
import {
  applyThemePreference,
  getStoredTheme,
  getThemePreference,
  setThemePreference,
  subscribeSystemTheme,
  type ThemeMode
} from "@/design-system/tokens";

const nav = [
  { to: "/", label: "Dashboard", icon: Home, accent: "text-[oklch(var(--co-brand-500))]" },
  { to: "/people", label: "People", icon: Users, accent: "text-[oklch(var(--co-sky-500))]" },
  { to: "/orgs", label: "Organizations", icon: Building2, accent: "text-[oklch(var(--co-emerald-500))]" },
  { to: "/tags", label: "Tags", icon: Tags, accent: "text-[oklch(var(--co-amber-500))]" },
  { to: "/graph", label: "Graph", icon: Network, accent: "text-[oklch(var(--co-fuchsia-500))]" },
  { to: "/review", label: "Review Queue", icon: ListChecks, accent: "text-[oklch(var(--co-rose-500))]" }
] as const;

const adminNav = [
  { to: "/tenants", label: "Tenants", icon: Building2, accent: "text-[oklch(var(--co-sky-500))]" },
  { to: "/settings", label: "Settings", icon: Settings, accent: "text-muted-foreground" }
] as const;

/** The gradient logo mark + wordmark lockup — the brand signature. */
function BrandLockup() {
  return (
    <Link to="/" className="focus-ring flex items-center gap-3 rounded-md" aria-label="Contact-Ops home">
      <span className="bg-gradient-brand flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-primary-foreground shadow-[var(--shadow-1)]">
        <BookUser className="h-5 w-5" strokeWidth={1.8} />
      </span>
      <span className="leading-tight">
        <span className="block text-base font-semibold tracking-tight">Contact-Ops</span>
        <span className="block text-xs text-muted-foreground">Identity hub</span>
      </span>
    </Link>
  );
}

function NavItem({
  to,
  label,
  icon: Icon,
  accent,
  active,
  onNavigate
}: {
  to: string;
  label: string;
  icon: typeof Home;
  accent: string;
  active: boolean;
  onNavigate?: () => void;
}) {
  return (
    <Link
      to={to}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(
        "focus-ring flex items-center gap-3 rounded-lg border-l-2 border-transparent px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "border-primary bg-primary/10 text-foreground"
          : "text-muted-foreground hover:bg-muted hover:text-foreground"
      )}
    >
      <Icon className={cn("h-[18px] w-[18px] shrink-0", accent)} strokeWidth={1.8} />
      {label}
    </Link>
  );
}

function SidebarContent({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  const { user, signoutRedirect } = useAuth();
  const profile = user?.profile as Record<string, unknown> | undefined;
  const name = String(profile?.name ?? profile?.preferred_username ?? profile?.email ?? "Signed in");
  const role = profile?.role ? String(profile.role) : "Member";
  const isActive = (to: string) => (to === "/" ? pathname === "/" : pathname.startsWith(to));

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center border-b px-4">
        <BrandLockup />
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto p-3">
        {nav.map((item) => (
          <NavItem key={item.to} {...item} active={isActive(item.to)} onNavigate={onNavigate} />
        ))}

        <div className="px-3 pb-1.5 pt-4">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Admin</p>
        </div>
        {adminNav.map((item) => (
          <NavItem key={item.to} {...item} active={isActive(item.to)} onNavigate={onNavigate} />
        ))}
      </nav>

      <div className="border-t p-3">
        <div className="flex items-center gap-3 px-1 py-2">
          <span className="co-human-sigil flex h-9 w-9 shrink-0 items-center justify-center text-sm">
            {initials(name)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{name}</p>
            <span className="mt-0.5 inline-flex items-center rounded-full bg-primary/10 px-1.5 py-0.5 text-[11px] font-medium text-primary">
              {role}
            </span>
          </div>
        </div>
        <Button
          variant="ghost"
          className="mt-1 w-full justify-start gap-2 text-muted-foreground hover:text-destructive"
          onClick={() => void signoutRedirect()}
        >
          <LogOut className="h-4 w-4" strokeWidth={1.8} />
          Sign out
        </Button>
      </div>
    </div>
  );
}

export function AppShell() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const [theme, setTheme] = useState<ThemeMode>(() => getThemePreference());
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Defer mounting (and thus loading the lazy chunk) until the user first opens
  // the palette; keep it mounted afterwards so Radix can run its close animation.
  const [paletteMounted, setPaletteMounted] = useState(false);

  useEffect(() => {
    applyThemePreference(theme);
  }, [theme]);

  // Follow OS color-scheme changes until the user picks an explicit theme.
  useEffect(() => {
    return subscribeSystemTheme((systemTheme) => {
      if (getStoredTheme() === null) setTheme(systemTheme);
    });
  }, []);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Global ⌘K → command palette. Bound to the "global" scope, so it fires
  // everywhere EXCEPT inside the Review Queue, whose nested HotkeysProvider
  // switches the active scope to "inbox" and owns its own ⌘K verb palette.
  // enableOnFormTags lets ⌘K still open the palette from the top-bar search.
  useHotkeys(
    "mod+k",
    (event) => {
      event.preventDefault();
      setPaletteMounted(true);
      setPaletteOpen((prev) => !prev);
    },
    { scopes: "global", enableOnFormTags: true, enableOnContentEditable: true },
    []
  );

  const dark = theme === "dark";
  const openPalette = () => {
    setPaletteMounted(true);
    setPaletteOpen(true);
  };

  return (
    <div className="min-h-screen bg-background">
      {/* Mobile drawer + backdrop */}
      {mobileOpen ? (
        <button
          type="button"
          aria-label="Close navigation"
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 w-64 border-r bg-[oklch(var(--sidebar))] transition-transform duration-200 lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        )}
      >
        <SidebarContent pathname={pathname} onNavigate={() => setMobileOpen(false)} />
      </aside>

      <div className="lg:pl-64">
        <header className="co-glass sticky top-0 z-30 flex h-16 items-center gap-3 border-b px-4 lg:px-6">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
          >
            <Menu className="h-5 w-5" strokeWidth={1.8} />
          </Button>

          <TenantSwitcher />

          {/* Global search entry — opens the ⌘K command palette. Rendered as a
              button (not a live input) so a single search surface drives the
              whole app; the palette owns the actual query field. */}
          <button
            type="button"
            onClick={openPalette}
            aria-label="Search people, organizations, and tags. Opens the command palette."
            aria-keyshortcuts="Meta+K Control+K"
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
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden"
              onClick={openPalette}
              aria-label="Open command palette"
            >
              <Search className="h-4 w-4" strokeWidth={1.8} />
            </Button>
            <Button variant="ghost" size="icon" title="Notifications" aria-label="Notifications">
              <Bell className="h-4 w-4" strokeWidth={1.8} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                const next = theme === "dark" ? "light" : "dark";
                setThemePreference(next);
                setTheme(next);
              }}
              title="Toggle color mode"
              aria-label="Toggle color mode"
              aria-pressed={dark}
            >
              {dark ? <Sun className="h-4 w-4" strokeWidth={1.8} /> : <Moon className="h-4 w-4" strokeWidth={1.8} />}
            </Button>
            <UserMenu />
          </div>
        </header>

        <main className="mx-auto max-w-7xl p-4 lg:p-6">
          <Outlet />
        </main>
      </div>

      {paletteMounted ? (
        <Suspense fallback={null}>
          <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
        </Suspense>
      ) : null}
    </div>
  );
}
