/**
 * DashboardHeroPreview — DEV-ONLY, auth-free, backend-free preview of the REAL
 * v2 dashboard command-center hero.
 * ============================================================================
 * The live Dashboard hero is auth-gated (it reads the OIDC profile name + MCP
 * metrics) so it can't be seen without a Keycloak session. This route mounts the
 * EXACT production components — `CommandCenterHero` + `HeroStat` exported from
 * routes/pages/Dashboard.tsx — with representative counts, so the hero's visual
 * design (ambient relationship graph, bloom, layered-glass count-up tiles, the
 * Ask-the-Analyst / Review-queue actions, dark/light luminosity) can be reviewed
 * offline. It is NOT the data-wired page; it's the same hero shell fed sample
 * numbers, and it's clearly labeled as such so the preview never implies these
 * are a real tenant's figures.
 *
 * Parity with the other previews: honors `?theme=light|dark` and the `T` key to
 * toggle, and `?empty=1` to preview the no-pending-data tile set (pending tile
 * omitted, exactly as the live page omits it when the metric is unavailable).
 *
 * Route: /design-system/dashboard-hero (registered in routes/index.tsx, DEV only).
 */
import { useEffect, useState } from "react";
import { Building2, ListChecks, Moon, Sparkles, Sun, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AppShellProvider } from "@/components/app-shell-context";
import { CommandCenterHero, HeroStat } from "@/routes/pages/Dashboard";
import { applyThemePreference, getThemePreference, type ThemeMode } from "@/design-system/tokens";

export function DashboardHeroPreview() {
  const [theme, setTheme] = useState<ThemeMode>(() => {
    const requested = new URLSearchParams(window.location.search).get("theme");
    return requested === "light" || requested === "dark" ? requested : getThemePreference();
  });
  // ?empty=1 → preview the tile set when the pending-review metric hasn't
  // resolved (the live page omits that tile rather than show a fake 0).
  const empty = (() => {
    const v = new URLSearchParams(window.location.search).get("empty");
    return v === "1" || v === "true";
  })();

  useEffect(() => {
    applyThemePreference(theme);
  }, [theme]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (!typing && event.key.toLowerCase() === "t" && !event.metaKey && !event.ctrlKey && !event.altKey) {
        setTheme((value) => (value === "dark" ? "light" : "dark"));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const nextTheme = theme === "dark" ? "light" : "dark";

  // Representative figures (clearly labeled as sample data below). The live page
  // feeds these from useDashboard()/useInboxList() instead.
  const tiles = (
    <>
      <HeroStat icon={Users} value={4218} label="People" />
      <HeroStat icon={Building2} value={612} label="Organizations" />
      {empty ? null : (
        <HeroStat icon={ListChecks} value={37} label="Pending review" sub="awaiting your decision" />
      )}
      <HeroStat icon={Sparkles} value={4} label="Agents active" sub="proposing changes" />
    </>
  );

  return (
    <main className="min-h-screen bg-background p-4 text-foreground lg:p-8">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-5">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
              Contact-Ops · dev preview
            </p>
            <h1 className="text-2xl font-semibold tracking-tight">Dashboard command-center hero</h1>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              The real <code>CommandCenterHero</code> component, fed sample counts (not a live tenant).
              Press <kbd className="rounded border border-border bg-muted px-1 font-mono text-[11px]">T</kbd> or
              use the button to toggle dark/light and check the ambient-graph luminosity in both. Append{" "}
              <code>?empty=1</code> to preview the tile set with no pending-review metric.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setTheme(nextTheme)}
            aria-label={`Switch to ${nextTheme} mode`}
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            <span className="capitalize">{nextTheme}</span>
          </Button>
        </header>

        {/* Provide a stub shell context so the hero's "Ask the Analyst" button is
            wired exactly like production — here it just logs, since the real
            AppShell (which owns the Analyst panel) isn't mounted in the preview. */}
        <AppShellProvider
          value={{
            openAnalyst: () => console.info("[preview] Ask the Analyst → opens the AI Analyst panel in the live app"),
            openPalette: () => console.info("[preview] open command palette")
          }}
        >
          <CommandCenterHero
            greeting="Good morning"
            name="Aaron"
            tiles={tiles}
            onAskAnalyst={() =>
              console.info("[preview] Ask the Analyst → opens the AI Analyst panel in the live app")
            }
          />
        </AppShellProvider>

        <p className="text-center text-xs text-muted-foreground">
          Sample data for visual review only — the live dashboard wires these tiles to real registry metrics.
        </p>
      </div>
    </main>
  );
}

export default DashboardHeroPreview;
