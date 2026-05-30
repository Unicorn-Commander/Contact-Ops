import { useEffect, useState, type ReactNode } from "react";
import { BookUser, ListChecks, Moon, Sparkles, Sun } from "lucide-react";
import { ActionAttribution } from "@/design-system/ActionAttribution";
import { ShellPreview } from "@/design-system/ShellPreview";
import { ScreensPreview } from "@/design-system/ScreensPreview";
import { CommandPalettePreview } from "@/design-system/CommandPalettePreview";
import { AIAnalystPreview } from "@/design-system/AIAnalystPreview";
import { NotesPreview } from "@/design-system/NotesPreview";
import { ImportPreview } from "@/design-system/ImportPreview";
import { ConnectorsPreview } from "@/design-system/ConnectorsPreview";
import { HelpConnectorsPreview } from "@/design-system/HelpConnectorsPreview";
import { BulkApprovePreview } from "@/design-system/BulkApprovePreview";
import { PATPreview } from "@/design-system/PATPreview";
import { Card, CardContent } from "@/components/ui/card";
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
          <KeyboardHint keys="mod+K" label="Command command" />
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

function GradientGallery({ theme }: { theme: ThemeMode }) {
  return (
    <div data-theme={theme} className="rounded-[var(--radius-lg)] border border-border bg-background p-4 text-foreground shadow-[var(--shadow-2)]">
      <div className="mb-4">
        <h2 className="text-base font-semibold capitalize">{theme} mode</h2>
        <p className="text-xs text-muted-foreground">Brand gradients, surface washes, and elevated header treatments.</p>
      </div>

      <div className="space-y-3">
        <StateBlock title="Brand Gradient (purple → fuchsia)">
          <div className="h-10 w-full rounded-md bg-gradient-brand" />
        </StateBlock>

        <StateBlock title="Logo Mark">
          <span className="bg-gradient-brand co-glow-brand flex h-9 w-9 items-center justify-center rounded-xl text-primary-foreground">
            <Sparkles className="h-4 w-4" />
          </span>
        </StateBlock>

        <StateBlock title="Gradient Button">
          <Button variant="gradient" size="sm">
            <Sparkles className="h-3.5 w-3.5" />
            Primary Action
          </Button>
        </StateBlock>

        <StateBlock title="Gradient Text">
          <span className="text-lg font-semibold bg-gradient-brand-text">Contact-Ops</span>
        </StateBlock>

        <StateBlock title="Surfaces (flat, cool neutral)">
          <Swatch label="background" className="bg-background" />
          <Swatch label="card" className="bg-card" />
          <Swatch label="muted" className="bg-muted" />
          <Swatch label="primary" className="bg-primary" />
          <Swatch label="fuchsia" className="bg-[oklch(var(--accent-fuchsia))]" />
        </StateBlock>
      </div>
    </div>
  );
}

function LivingDataGallery({ theme }: { theme: ThemeMode }) {
  return (
    <div data-theme={theme} className="rounded-[var(--radius-lg)] border border-border bg-background p-4 text-foreground shadow-[var(--shadow-2)]">
      <div className="mb-4">
        <h2 className="text-base font-semibold capitalize">{theme} mode</h2>
        <p className="text-xs text-muted-foreground">Living data: agent vs human, confidence tiers, and item status.</p>
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <StateBlock title="Agent Indicator">
          <span className="co-agent-indicator inline-flex h-6 w-6 items-center justify-center rounded-md border">
            <ListChecks className="h-3 w-3 text-[oklch(var(--co-agent-color))]" />
          </span>
          <AgentBadge slug="dedupe-agent" />
        </StateBlock>

        <StateBlock title="Human Indicator">
          <span className="co-human-indicator inline-flex h-6 w-6 items-center justify-center rounded-full border">
            <span className="text-[9px] font-semibold">AS</span>
          </span>
          <HumanBadge name="Aaron Stransky" />
        </StateBlock>

        <StateBlock title="Confidence Tiers">
          <span className="inline-flex h-2 w-2 rounded-full bg-[oklch(var(--co-emerald-500))]" />
          <span className="inline-flex h-2 w-2 rounded-full bg-[oklch(var(--co-sky-500))]" />
          <span className="inline-flex h-2 w-2 rounded-full bg-[oklch(var(--co-amber-500))]" />
          <span className="inline-flex h-2 w-2 rounded-full bg-[oklch(var(--co-rose-500))]" />
        </StateBlock>

        <StateBlock title="Item Status">
          <span className="rounded px-2 py-0.5 text-[10px] font-medium co-status-proposed">Proposed</span>
          <span className="rounded px-2 py-0.5 text-[10px] font-medium co-status-applied">Applied</span>
          <span className="rounded px-2 py-0.5 text-[10px] font-medium co-status-rejected">Rejected</span>
          <span className="rounded px-2 py-0.5 text-[10px] font-medium co-status-snoozed">Snoozed</span>
        </StateBlock>
      </div>
    </div>
  );
}

function MotionGallery({ theme }: { theme: ThemeMode }) {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const interval = setInterval(() => setVisible((v) => !v), 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div data-theme={theme} className="rounded-[var(--radius-lg)] border border-border bg-background p-4 text-foreground shadow-[var(--shadow-2)]">
      <div className="mb-4">
        <h2 className="text-base font-semibold capitalize">{theme} mode</h2>
        <p className="text-xs text-muted-foreground">Micro-interactions: fade-in, slide-up, pulse, shimmer.</p>
      </div>

      <div className="space-y-3">
        <StateBlock title="Fade In">
          <div className="flex items-center gap-2">
            {visible && (
              <span className="co-animate-fade-in inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs">
                <ListChecks className="h-3 w-3" />
                Fade in
              </span>
            )}
          </div>
        </StateBlock>

        <StateBlock title="Slide Up">
          <div className="flex items-center gap-2">
            {visible && (
              <span className="co-animate-slide-up inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs">
                <ListChecks className="h-3 w-3" />
                Slide up
              </span>
            )}
          </div>
        </StateBlock>

        <StateBlock title="Pulse Subtle">
          <span className="co-animate-pulse-subtle inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs">
            <ListChecks className="h-3 w-3" />
            Pulse
          </span>
        </StateBlock>

        <StateBlock title="Shimmer">
          <div className="h-8 w-48 rounded-md co-shimmer" />
        </StateBlock>
      </div>
    </div>
  );
}

/** Auth-free twin of the AuthGate login screen (routes/index.tsx). */
function LoginPreview({ theme }: { theme: ThemeMode }) {
  return (
    <div
      data-theme={theme}
      className="flex min-h-[560px] items-center justify-center rounded-[var(--radius-lg)] border border-border bg-background p-4 text-foreground shadow-[var(--shadow-2)]"
    >
      <div className="w-full max-w-md">
        <Card className="border-border">
          <CardContent className="px-8 py-10 text-center">
            <div className="bg-gradient-brand co-glow-brand mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-2xl text-primary-foreground">
              <BookUser className="h-7 w-7" strokeWidth={1.6} />
            </div>
            <h1 className="bg-gradient-brand-text text-3xl font-bold tracking-tight">Contact-Ops</h1>
            <p className="mx-auto mt-2.5 max-w-xs text-sm leading-relaxed text-muted-foreground">
              The canonical identity hub. Sign in with Magic Unicorn to manage people, organizations, and the agents
              that keep them clean.
            </p>
            <Button className="mt-8 h-11 w-full" variant="gradient">
              Sign in
            </Button>
            <p className="mt-4 text-xs text-muted-foreground">Secured by Magic Unicorn SSO</p>
          </CardContent>
        </Card>
        <p className="mt-4 text-center text-xs text-muted-foreground">by Magic Unicorn</p>
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
    const valid = ["shell", "login", "primitives", "gradients", "living-data", "motion", "inbox", "graph"] as const;
    return valid.includes(requested as typeof valid[number]) ? (requested as string) : "shell";
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
            <TabsList className="flex h-auto flex-wrap">
              <TabsTrigger value="shell">Shell + Dashboard</TabsTrigger>
              <TabsTrigger value="screens">Screens</TabsTrigger>
              <TabsTrigger value="cmdk">⌘K Palette</TabsTrigger>
              <TabsTrigger value="ai-analyst">AI Analyst</TabsTrigger>
              <TabsTrigger value="notes">Notes & Log</TabsTrigger>
              <TabsTrigger value="import">Import</TabsTrigger>
              <TabsTrigger value="connectors">Connectors</TabsTrigger>
              <TabsTrigger value="bulk-approve">Bulk Approve</TabsTrigger>
              <TabsTrigger value="pats">PATs</TabsTrigger>
              <TabsTrigger value="connectors-help">Connector Help</TabsTrigger>
              <TabsTrigger value="login">Login</TabsTrigger>
              <TabsTrigger value="primitives">Primitives</TabsTrigger>
              <TabsTrigger value="gradients">Gradients</TabsTrigger>
              <TabsTrigger value="living-data">Living Data</TabsTrigger>
              <TabsTrigger value="motion">Motion</TabsTrigger>
              <TabsTrigger value="inbox">Review Queue</TabsTrigger>
              <TabsTrigger value="graph">Graph Labels</TabsTrigger>
            </TabsList>
            <TabsContent value="shell" className="space-y-6">
              <div className="space-y-2">
                <h2 className="text-base font-semibold">Command center — dark</h2>
                <ShellPreview theme="dark" />
              </div>
              <div className="space-y-2">
                <h2 className="text-base font-semibold">Command center — light</h2>
                <ShellPreview theme="light" />
              </div>
              <div className="space-y-2">
                <h2 className="text-base font-semibold">Mobile (375px, sidebar collapsed)</h2>
                <div className="mx-auto w-[375px] max-w-full">
                  <ShellPreview theme="dark" compact />
                </div>
              </div>
            </TabsContent>
            <TabsContent value="screens" className="space-y-6">
              <div className="space-y-2">
                <h2 className="text-base font-semibold">Inner screens — dark</h2>
                <ScreensPreview theme="dark" />
              </div>
              <div className="space-y-2">
                <h2 className="text-base font-semibold">Inner screens — light</h2>
                <ScreensPreview theme="light" />
              </div>
            </TabsContent>
            <TabsContent value="cmdk" className="space-y-6">
              <div className="grid gap-4 2xl:grid-cols-2">
                <div className="space-y-2">
                  <h2 className="text-base font-semibold">Command palette — dark</h2>
                  <CommandPalettePreview theme="dark" mode="search" />
                </div>
                <div className="space-y-2">
                  <h2 className="text-base font-semibold">Command palette — light</h2>
                  <CommandPalettePreview theme="light" mode="search" />
                </div>
              </div>
            </TabsContent>
            <TabsContent value="ai-analyst">
              <AIAnalystPreview />
            </TabsContent>
            <TabsContent value="import">
              <ImportPreview />
            </TabsContent>
            <TabsContent value="connectors">
              <ConnectorsPreview />
            </TabsContent>
            <TabsContent value="connectors-help">
              <HelpConnectorsPreview />
            </TabsContent>
            <TabsContent value="bulk-approve">
              <BulkApprovePreview />
            </TabsContent>
            <TabsContent value="pats">
              <PATPreview />
            </TabsContent>
            <TabsContent value="notes">
              <NotesPreview />
            </TabsContent>
            <TabsContent value="login" className="space-y-4">
              <div className="grid gap-4 2xl:grid-cols-2">
                <LoginPreview theme="dark" />
                <LoginPreview theme="light" />
              </div>
            </TabsContent>
            <TabsContent value="primitives" className="space-y-4">
              <div className="grid gap-4 2xl:grid-cols-2">
                <PrimitiveGallery theme="dark" />
                <PrimitiveGallery theme="light" />
              </div>
            </TabsContent>
            <TabsContent value="gradients" className="space-y-4">
              <div className="grid gap-4 2xl:grid-cols-2">
                <GradientGallery theme="dark" />
                <GradientGallery theme="light" />
              </div>
            </TabsContent>
            <TabsContent value="living-data" className="space-y-4">
              <div className="grid gap-4 2xl:grid-cols-2">
                <LivingDataGallery theme="dark" />
                <LivingDataGallery theme="light" />
              </div>
            </TabsContent>
            <TabsContent value="motion" className="space-y-4">
              <div className="grid gap-4 2xl:grid-cols-2">
                <MotionGallery theme="dark" />
                <MotionGallery theme="light" />
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
