import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { Bell, Building2, Home, ListChecks, Moon, Network, Settings, Tags, Sun, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { TenantSwitcher } from "@/components/domain/TenantSwitcher";
import { cn } from "@/lib/utils";
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
  { to: "/review", label: "Review Queue", icon: ListChecks },
  { to: "/tenants", label: "Tenants", icon: Building2 },
  { to: "/settings", label: "Settings", icon: Settings }
] as const;

export function AppShell() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const [theme, setTheme] = useState<ThemeMode>(() => getThemePreference());

  useEffect(() => {
    applyThemePreference(theme);
  }, [theme]);

  // Follow OS color-scheme changes until the user picks an explicit theme.
  useEffect(() => {
    return subscribeSystemTheme((systemTheme) => {
      if (getStoredTheme() === null) setTheme(systemTheme);
    });
  }, []);

  const dark = theme === "dark";

  return (
    <div className="min-h-screen bg-background">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r bg-card lg:block">
        <div className="flex h-16 items-center border-b px-5">
          <div>
            <p className="text-lg font-semibold">Contact-Ops</p>
            <p className="text-xs text-muted-foreground">Canonical registry</p>
          </div>
        </div>
        <nav className="space-y-1 p-3">
          {nav.map((item) => {
            const Icon = item.icon;
            const active = item.to === "/" ? pathname === "/" : pathname.startsWith(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground",
                  active && "bg-primary/10 text-foreground border-l-2 border-primary"
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b bg-background/95 px-4 backdrop-blur lg:px-6">
          <div className="flex items-center gap-3">
            <TenantSwitcher />
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" title="Notifications">
              <Bell className="h-4 w-4" />
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
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
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
