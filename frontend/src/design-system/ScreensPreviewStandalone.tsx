/**
 * Standalone, dev-only mount for {@link ScreensPreview}. Lets the polished inner
 * screens be screenshotted in isolation without auth/router/MCP — served at
 * /screens-preview.html by the Vite dev server. Reads `?theme=light|dark` (and
 * `?screen=` to focus one screen); press `T` to toggle theme.
 *
 * This is a screenshotting aid, not a shipped surface: it is not wired into the
 * app router and the production build only bundles index.html, so it never
 * affects `npm run build`. The canonical wiring is the /design-system route,
 * which the showcase owner threads into Showcase.tsx separately.
 */
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { KeyboardHint } from "@/design-system/KeyboardHint";
import {
  EmptyStatesPreview,
  GraphPreview,
  OrgsPreview,
  PeoplePreview,
  ReviewQueuePreview,
  ScreensPreview,
  SettingsPreview,
  TagsPreview,
  TenantsPreview
} from "@/design-system/ScreensPreview";
import { applyThemePreference, getThemePreference, type ThemeMode } from "@/design-system/tokens";
import { Moon, Sun } from "lucide-react";

const SCREENS = {
  all: ScreensPreview,
  people: (p: { theme: ThemeMode }) => <PeoplePreview {...p} />,
  "people-grid": (p: { theme: ThemeMode }) => <PeoplePreview {...p} layout="grid" />,
  orgs: (p: { theme: ThemeMode }) => <OrgsPreview {...p} />,
  tags: TagsPreview,
  tenants: TenantsPreview,
  settings: SettingsPreview,
  review: ReviewQueuePreview,
  graph: GraphPreview,
  empty: EmptyStatesPreview
} as const;

type ScreenKey = keyof typeof SCREENS;

export function ScreensPreviewStandalone() {
  const params = new URLSearchParams(window.location.search);
  const initialTheme = params.get("theme");
  const requestedScreen = params.get("screen") as ScreenKey | null;
  const screen: ScreenKey = requestedScreen && requestedScreen in SCREENS ? requestedScreen : "all";

  const [theme, setTheme] = useState<ThemeMode>(
    initialTheme === "light" || initialTheme === "dark" ? initialTheme : getThemePreference()
  );

  useEffect(() => {
    applyThemePreference(theme);
  }, [theme]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "t" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        setTheme((v) => (v === "dark" ? "light" : "dark"));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const Screen = SCREENS[screen];
  const next = theme === "dark" ? "light" : "dark";

  return (
    <main className="min-h-screen bg-background p-4 text-foreground lg:p-6">
      <div className="mx-auto max-w-[1500px] space-y-4">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Contact-Ops</p>
            <h1 className="text-2xl font-semibold tracking-tight">
              Inner screens — <span className="capitalize">{screen === "all" ? "all" : screen.replace("-", " ")}</span>
            </h1>
          </div>
          <div className="flex items-center gap-2">
            <KeyboardHint keys="T" label="Toggle theme" />
            <Button variant="outline" size="sm" onClick={() => setTheme(next)} aria-label={`Switch to ${next} mode`}>
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              <span className="capitalize">{next}</span>
            </Button>
          </div>
        </header>
        <Screen theme={theme} />
      </div>
    </main>
  );
}

export default ScreensPreviewStandalone;
