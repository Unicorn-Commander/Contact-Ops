import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
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
import { useEffect, useState } from "react";
import { useAuth } from "react-oidc-context";
import { Button } from "@/components/ui/button";
import { TenantSwitcher } from "@/components/domain/TenantSwitcher";
import { cn, initials } from "@/lib/utils";
import { UserMenu } from "@/components/UserMenu";
import {
  applyThemePreference,
  getStoredTheme,
  getThemePreference,
  setThemePreference,
  subscribeSystemTheme,
  type ThemeMode
} from "@/design-system/tokens";

const nav = [
  { to: "/", label: "Dashboard", icon: Home },
  { to: "/people", label: "People", icon: Users },
  { to: "/orgs", label: "Organizations", icon: Building2 },
  { to: "/tags", label: "Tags", icon: Tags },
  { to: "/graph", label: "Graph", icon: Network },
  { to: "/review", label: "Review Queue", icon: ListChecks }
] as const;

const adminNav = [
  { to: "/tenants", label: "Tenants", icon: Building2 },
  { to: "/settings", label: "Settings", icon: Settings }
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
  active,
  onNavigate
}: {
  to: string;
  label: string;
  icon: typeof Home;
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
      <Icon className={cn("h-[18px] w-[18px]", active && "text-primary")} strokeWidth={1.8} />
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
  const navigate = useNavigate();
  const [theme, setTheme] = useState<ThemeMode>(() => getThemePreference());
  const [mobileOpen, setMobileOpen] = useState(false);
  const [search, setSearch] = useState("");

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

  const dark = theme === "dark";

  // Global search entry: the People directory is the canonical search surface,
  // so submitting jumps there. (A future cross-entity command palette can hang
  // off this same input.) Routing stays type-safe — no undeclared search schema.
  const submitSearch = () => {
    void navigate({ to: "/people" });
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
          "fixed inset-y-0 left-0 z-50 w-64 border-r bg-card transition-transform duration-200 lg:translate-x-0",
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

          {/* Global search entry */}
          <form
            className="relative ml-auto hidden max-w-sm flex-1 md:block"
            onSubmit={(event) => {
              event.preventDefault();
              submitSearch();
            }}
            role="search"
          >
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
              strokeWidth={1.8}
            />
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search people, orgs, tags…"
              aria-label="Search people, organizations, and tags"
              className="focus-ring h-9 w-full rounded-md border bg-background/60 pl-9 pr-3 text-sm placeholder:text-muted-foreground"
            />
          </form>

          <div className="ml-auto flex items-center gap-1 md:ml-0">
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden"
              onClick={submitSearch}
              aria-label="Search"
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
    </div>
  );
}
