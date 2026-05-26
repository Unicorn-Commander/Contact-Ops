import { useEffect, useState, type ReactNode } from "react";
import { Moon, Sun } from "lucide-react";
import { ActionAttribution } from "@/design-system/ActionAttribution";
import { AgentBadge } from "@/design-system/AgentBadge";
import { ConfidenceIndicator } from "@/design-system/ConfidenceIndicator";
import { DensityControl, DensityProvider } from "@/design-system/Density";
import { GraphLabelStressTest } from "@/design-system/GraphLabelStressTest";
import { HumanBadge } from "@/design-system/HumanBadge";
import { InboxStressTest } from "@/design-system/InboxStressTest";
import { KeyboardHint } from "@/design-system/KeyboardHint";
import { MonoNumeric } from "@/design-system/MonoNumeric";
import { Pulse } from "@/design-system/Pulse";
import { Spinner } from "@/design-system/Spinner";
import { Streaming } from "@/design-system/Streaming";
import { applyThemePreference, getThemePreference, type ThemeMode } from "@/design-system/tokens";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const now = Date.now();

function Swatch({ label, className }: { label: string; className: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className={cn("h-6 w-6 rounded-[var(--radius-sm)] border border-border", className)} />
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

function StateBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-[var(--radius-md)] border border-border bg-card p-3 shadow-[var(--shadow-1)]">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</h3>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

function PrimitiveGallery({ theme }: { theme: ThemeMode }) {
  return (
    <div data-theme={theme} className="rounded-[var(--radius-lg)] border border-border bg-background p-4 text-foreground shadow-[var(--shadow-2)]">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold capitalize">{theme} mode</h2>
          <p className="text-xs text-muted-foreground">Geist, OKLCH, attribution, confidence, density, and agent state primitives.</p>
        </div>
        <DensityControl />
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        <StateBlock title="Agent Badge">
          <AgentBadge slug="dedupe-agent" />
          <AgentBadge slug="voice-match-agent" status="thinking" />
          <AgentBadge slug="enrichment-agent" status="waiting" />
          <AgentBadge slug="carddav-reconcile" status="done" />
          <AgentBadge slug="tag-agent" status="error" />
          <AgentBadge slug="merge-proposer-agent" disabled />
        </StateBlock>
        <StateBlock title="Human Badge">
          <HumanBadge name="Aaron Stransky" />
          <HumanBadge name="Mina Chen" className="bg-muted" />
          <HumanBadge name="Rafael Ortiz" className="ring-2 ring-ring ring-offset-2 ring-offset-background" />
          <HumanBadge name="Ops Reviewer" disabled />
        </StateBlock>
        <StateBlock title="Confidence Indicator">
          <ConfidenceIndicator value={0.96} />
          <ConfidenceIndicator value={0.82} />
          <ConfidenceIndicator value={0.64} />
          <ConfidenceIndicator value={0.38} />
          <ConfidenceIndicator value={0.92} mode="numeric" />
          <ConfidenceIndicator value={0.41} loading />
          <ConfidenceIndicator value={0.22} error />
        </StateBlock>
        <StateBlock title="Action Attribution">
          <ActionAttribution actor={{ type: "human", name: "Aaron Stransky", label: "Aaron" }} verb="Approved" timestamp={now - 180000} />
          <ActionAttribution actor={{ type: "agent", slug: "enrichment-agent" }} verb="Auto-applied" timestamp={now - 14000} target="T3" />
          <ActionAttribution actor={{ type: "human", name: "Aaron Stransky", label: "Aaron" }} verb="Reverted" timestamp={now - 9000} compact />
        </StateBlock>
        <StateBlock title="Keyboard Hint">
          <KeyboardHint keys="mod+K" label="Command menu" />
          <KeyboardHint keys="Y" label="Approve" />
          <KeyboardHint keys={["shift", "J"]} label="Jump" />
          <KeyboardHint keys="esc" label="Close" />
          <KeyboardHint keys="mod+E" label="Edit" disabled />
        </StateBlock>
        <StateBlock title="Loading">
          <Spinner label="Fetching" />
          <Pulse agentSlug="relationship-agent" label="Thinking" />
          <Streaming text="Writing proposal" />
          <Streaming text="Complete" complete />
        </StateBlock>
        <StateBlock title="Mono Numeric">
          <MonoNumeric>1,204</MonoNumeric>
          <MonoNumeric tone="muted">0.94</MonoNumeric>
          <MonoNumeric tone="strong">P-0137</MonoNumeric>
        </StateBlock>
        <StateBlock title="Color">
          <Swatch label="emerald" className="bg-[oklch(var(--co-emerald-500))]" />
          <Swatch label="sky" className="bg-[oklch(var(--co-sky-500))]" />
          <Swatch label="amber" className="bg-[oklch(var(--co-amber-500))]" />
          <Swatch label="rose" className="bg-[oklch(var(--co-rose-500))]" />
          <Swatch label="brand" className="bg-[oklch(var(--co-brand-500))]" />
        </StateBlock>
      </div>
    </div>
  );
}

export function DesignSystemShowcase() {
  const [theme, setTheme] = useState<ThemeMode>(() => {
    const requested = new URLSearchParams(window.location.search).get("theme");
    return requested === "light" || requested === "dark" ? requested : getThemePreference();
  });
  const [activeTab, setActiveTab] = useState(() => {
    const requested = new URLSearchParams(window.location.search).get("tab");
    return requested === "inbox" || requested === "graph" ? requested : "primitives";
  });

  useEffect(() => {
    applyThemePreference(theme);
  }, [theme]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "t" && !event.metaKey && !event.ctrlKey && !event.altKey) {
        setTheme((value) => (value === "dark" ? "light" : "dark"));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const nextTheme = theme === "dark" ? "light" : "dark";

  return (
    <DensityProvider defaultDensity="compact">
      <main className="min-h-screen bg-background p-4 text-foreground lg:p-6">
        <div className="mx-auto max-w-[1500px] space-y-4">
          <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Contact-Ops</p>
              <h1 className="text-2xl font-semibold tracking-normal">Design System Stage 0</h1>
            </div>
            <div className="flex items-center gap-2">
              <KeyboardHint keys="T" label="Toggle theme" />
              <Button variant="outline" size="sm" onClick={() => setTheme(nextTheme)} aria-label={`Switch to ${nextTheme} mode`}>
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                <span className="capitalize">{nextTheme}</span>
              </Button>
            </div>
          </header>

          <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
            <TabsList>
              <TabsTrigger value="primitives">Primitives</TabsTrigger>
              <TabsTrigger value="inbox">Inbox Stress</TabsTrigger>
              <TabsTrigger value="graph">Graph Labels</TabsTrigger>
            </TabsList>
            <TabsContent value="primitives" className="space-y-4">
              <div className="grid gap-4 2xl:grid-cols-2">
                <PrimitiveGallery theme="dark" />
                <PrimitiveGallery theme="light" />
              </div>
            </TabsContent>
            <TabsContent value="inbox">
              <InboxStressTest />
            </TabsContent>
            <TabsContent value="graph">
              <GraphLabelStressTest />
            </TabsContent>
          </Tabs>
        </div>
      </main>
    </DensityProvider>
  );
}

export default DesignSystemShowcase;
