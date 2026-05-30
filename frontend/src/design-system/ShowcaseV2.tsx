/**
 * ShowcaseV2 — Design Language v2 showcase (DEV-ONLY).
 * =====================================================
 * A single page proposing a bolder "world-class / near-future" visual direction
 * for Contact-Ops, for Aaron to approve BEFORE any real-page refactor. This is a
 * SHOWCASE, not an app refactor — it imports tokens-v2.css (additive) and the
 * v2/* components, and touches nothing in the live app.
 *
 * The whole point is the DUAL-INTENSITY principle ("only add value, never
 * subtract"):
 *   - CINEMATIC on brand/command surfaces — login hero, dashboard hero, empty
 *     states: ambient glowing relationship graph + bloom + bold gradient
 *     wordmark + layered glass.
 *   - CALM + FAST on dense data surfaces — people-list cards, review-queue rows:
 *     glass-calm, tasteful, AA-legible, zero readability cost.
 * Both are shown so the contrast is visible side by side.
 *
 * Accessibility: every animation (particles, parallax-free drift, count-ups,
 * bloom pulse, wordmark pan) is gated behind prefers-reduced-motion — the graph
 * canvas paints one static frame, count-ups render final values, and all v2
 * keyframes are disabled in tokens-v2.css. All text targets WCAG AA.
 *
 * Route: /design-system/v2 (registered in routes/index.tsx, DEV only).
 */
import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  Bot,
  Building2,
  Check,
  Command,
  Mail,
  Moon,
  Network,
  Search,
  Sparkles,
  Sun,
  Users,
  Wand2,
  X
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { applyThemePreference, getThemePreference, type ThemeMode } from "@/design-system/tokens";
import { AmbientGraph } from "@/design-system/v2/AmbientGraph";
import { CountUp } from "@/design-system/v2/CountUp";
import { ConfidenceMeterV2 } from "@/design-system/v2/ConfidenceMeterV2";
import { cn } from "@/lib/utils";
import "@/design-system/tokens-v2.css";

/* ------------------------------------------------------------------ Section */

function SectionHeading({
  index,
  title,
  blurb,
  intensity
}: {
  index: number;
  title: string;
  blurb: string;
  intensity: "cinematic" | "calm" | "neutral";
}) {
  const tone =
    intensity === "cinematic"
      ? { label: "Cinematic", cls: "border-[oklch(var(--co-fuchsia-500)/0.4)] text-[oklch(var(--co-fuchsia-300))]" }
      : intensity === "calm"
        ? { label: "Calm + fast", cls: "border-[oklch(var(--co-sky-500)/0.4)] text-[oklch(var(--co-sky-300))]" }
        : { label: "Components", cls: "border-border text-muted-foreground" };
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="flex items-baseline gap-3">
        <span className="co-mono-numeric font-mono text-sm text-muted-foreground">
          {String(index).padStart(2, "0")}
        </span>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
          <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">{blurb}</p>
        </div>
      </div>
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium",
          tone.cls
        )}
      >
        {tone.label}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------ 1. Login hero */

function LoginHero() {
  return (
    <div className="co-v2-ambient co-v2-vignette relative overflow-hidden rounded-[var(--radius-lg)] border border-[oklch(var(--co-v2-glass-hairline))] shadow-[var(--shadow-3)]">
      <AmbientGraph nodeCount={30} className="opacity-90" />
      <div className="co-v2-stage-content grid min-h-[560px] items-center gap-10 p-8 lg:grid-cols-2 lg:p-14">
        {/* Left — the brand promise */}
        <div className="max-w-xl">
          <span className="inline-flex items-center gap-2 rounded-full border border-[oklch(var(--co-v2-glass-hairline-strong))] bg-[oklch(var(--co-v2-glass-fill))] px-3 py-1 text-xs font-medium text-foreground backdrop-blur">
            <Network className="h-3.5 w-3.5 text-[oklch(var(--co-brand-300))]" strokeWidth={2} />
            The canonical identity graph
          </span>
          <h1 className="mt-6 text-4xl font-bold leading-[1.08] tracking-tight text-foreground lg:text-5xl">
            Every person.{" "}
            <span className="co-v2-wordmark co-v2-wordmark-animate">Every connection.</span>{" "}
            One source of truth.
          </h1>
          <p className="mt-5 max-w-md text-base leading-relaxed text-muted-foreground">
            Contact-Ops keeps your people, organizations, and the agents that maintain them clean,
            connected, and current — so the relationship graph is always one you can trust.
          </p>
        </div>

        {/* Right — the sign-in glass card, lifted on its own bloom */}
        <div className="relative mx-auto w-full max-w-md">
          <span
            className="co-v2-bloom co-v2-bloom-pulse"
            style={{ inset: "-12% -8% auto -8%", height: "60%" }}
            aria-hidden="true"
          />
          <div className="co-v2-glass co-v2-glass-edge relative p-8 text-center">
            <span
              className="co-v2-glow-strong bg-gradient-brand mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl text-primary-foreground"
              aria-hidden="true"
            >
              <Network className="h-8 w-8" strokeWidth={1.6} />
            </span>
            <h2 className="co-v2-wordmark text-3xl font-bold tracking-tight">Contact-Ops</h2>
            <p className="mx-auto mt-2.5 max-w-xs text-sm leading-relaxed text-muted-foreground">
              Sign in with Magic Unicorn to manage people, organizations, and the agents that keep
              them clean.
            </p>
            <Button className="co-v2-hover-bloom co-v2-glow-strong mt-8 h-12 w-full text-sm" variant="gradient">
              <Sparkles className="h-4 w-4" strokeWidth={2} />
              Sign in with Magic Unicorn
            </Button>
            <p className="mt-4 text-xs text-muted-foreground">Secured by Magic Unicorn SSO</p>
          </div>
          <p className="mt-4 text-center text-xs text-muted-foreground">by Magic Unicorn</p>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------- 2. Dashboard command center */

function StatCard({
  icon: Icon,
  value,
  decimals,
  suffix,
  label,
  sub
}: {
  icon: typeof Users;
  value: number;
  decimals?: number;
  suffix?: string;
  label: string;
  sub: string;
}) {
  return (
    <div className="co-v2-glass co-v2-glass-edge relative overflow-hidden p-5">
      <div className="flex items-center justify-between">
        <span className="flex h-9 w-9 items-center justify-center rounded-[var(--radius-md)] border border-[oklch(var(--co-brand-300)/0.3)] bg-[oklch(var(--co-brand-500)/0.12)] text-[oklch(var(--co-brand-300))]">
          <Icon className="h-4.5 w-4.5" strokeWidth={1.8} />
        </span>
        <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-muted-foreground">{label}</span>
      </div>
      <div className="mt-4 text-3xl font-bold tracking-tight text-foreground">
        <CountUp value={value} decimals={decimals} suffix={suffix} />
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{sub}</p>
    </div>
  );
}

function DashboardHero() {
  return (
    <div className="co-v2-ambient relative overflow-hidden rounded-[var(--radius-lg)] border border-[oklch(var(--co-v2-glass-hairline))] shadow-[var(--shadow-3)]">
      <AmbientGraph nodeCount={24} linkDistance={0.26} className="opacity-60" />
      <div className="co-v2-stage-content p-8 lg:p-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-sm text-muted-foreground">Good morning, Aaron</p>
            <h2 className="mt-1 text-3xl font-bold tracking-tight text-foreground">
              Your relationship graph at a glance
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="gradient" size="sm" className="co-v2-hover-bloom">
              <Wand2 className="h-3.5 w-3.5" strokeWidth={2} />
              Ask the Analyst
            </Button>
            <Button variant="outline" size="sm" className="border-[oklch(var(--co-v2-glass-hairline-strong))] bg-[oklch(var(--co-v2-glass-fill))] backdrop-blur">
              Review queue
            </Button>
          </div>
        </div>

        <div className="mt-7 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard icon={Users} value={4218} label="People" sub="+126 this month" />
          <StatCard icon={Building2} value={612} label="Orgs" sub="+18 this month" />
          <StatCard icon={Bot} value={37} label="Pending review" sub="9 high-confidence" />
          <StatCard icon={Network} value={98.4} decimals={1} suffix="%" label="Graph health" sub="dedup + enrichment current" />
        </div>
      </div>
    </div>
  );
}

/* ----------------------------------------------- 3. PersonCard (calm/curated) */

type DemoPerson = {
  name: string;
  email: string;
  org: string;
  temp: string;
  tags: string[];
  initials: string;
  last: string;
};

const demoPeople: DemoPerson[] = [
  {
    name: "Mina Chen",
    email: "REMOVED",
    org: "Orbital Systems",
    temp: "Warm",
    tags: ["investor", "intro-via-jason"],
    initials: "MC",
    last: "2 days ago"
  },
  {
    name: "Rafael Ortiz",
    email: "REMOVED",
    org: "Northwind Labs",
    temp: "Active",
    tags: ["partner", "design"],
    initials: "RO",
    last: "5 hours ago"
  }
];

function PersonCardV2({ person }: { person: DemoPerson }) {
  return (
    <div className="co-v2-glass-calm group flex items-center gap-4 p-4 transition-colors hover:border-[oklch(var(--co-brand-300)/0.4)]">
      <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[oklch(var(--co-brand-500)/0.16)] text-sm font-semibold text-[oklch(var(--co-brand-300))]">
        {person.initials}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate font-medium text-foreground">{person.name}</p>
          <span className="shrink-0 rounded-full border border-border bg-secondary px-2 py-0.5 text-[10px] font-medium capitalize text-secondary-foreground">
            {person.temp}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <Mail className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
            <span className="truncate">{person.email}</span>
          </span>
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <Building2 className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
            <span className="truncate">{person.org}</span>
          </span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {person.tags.map((tag) => (
            <span
              key={tag}
              className="rounded-full border border-[oklch(var(--co-brand-300)/0.25)] bg-[oklch(var(--co-brand-500)/0.08)] px-2 py-0.5 text-[11px] text-[oklch(var(--co-brand-300))]"
            >
              {tag}
            </span>
          ))}
        </div>
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-3 self-start pt-0.5">
        <span className="co-mono-numeric hidden text-xs text-muted-foreground sm:inline">{person.last}</span>
        <ArrowRight className="h-4 w-4 text-muted-foreground transition-colors group-hover:text-foreground" strokeWidth={1.6} />
      </div>
    </div>
  );
}

/* --------------------------------- 4. Review-queue proposal row (calm style) */

type DemoProposal = {
  id: string;
  agent: string;
  agentLabel: string;
  verb: string;
  detail: string;
  confidence: number;
  when: string;
  flag?: "cross-tenant" | "hipaa";
};

const demoProposals: DemoProposal[] = [
  {
    id: "p1",
    agent: "dedupe-agent",
    agentLabel: "Dedupe Agent v2.3",
    verb: "Merge",
    detail: "Mina Chen ← Mina C. (orbital.io)",
    confidence: 0.96,
    when: "12s ago"
  },
  {
    id: "p2",
    agent: "enrichment-agent",
    agentLabel: "Enrichment Agent v1.8",
    verb: "Set 3 fields",
    detail: "title, company, location for Rafael Ortiz",
    confidence: 0.82,
    when: "4m ago"
  },
  {
    id: "p3",
    agent: "voice-match-agent",
    agentLabel: "Voice Match v0.9",
    verb: "Match voice",
    detail: "Unknown speaker → Devon Walsh",
    confidence: 0.61,
    when: "9m ago",
    flag: "cross-tenant"
  },
  {
    id: "p4",
    agent: "relationship-agent",
    agentLabel: "Relationship Agent v1.1",
    verb: "Link relationship",
    detail: "Maya Hart — works-with — Shafen Khan",
    confidence: 0.44,
    when: "21m ago",
    flag: "hipaa"
  }
];

function ProposalRowV2({ proposal, focused }: { proposal: DemoProposal; focused?: boolean }) {
  return (
    <div
      className={cn(
        "co-v2-row co-v2-row-rail group grid min-h-[52px] cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-3 px-3 py-2.5 text-[13px]",
        focused && "border-[oklch(var(--co-brand-300)/0.5)] bg-[oklch(var(--co-brand-500)/0.08)]"
      )}
      style={{ "--co-v2-rail-color": "oklch(var(--co-agent-color))" } as React.CSSProperties}
      role="button"
      tabIndex={0}
      aria-current={focused ? "true" : undefined}
    >
      {/* Agent sigil — square-cornered, never human-shaped (AIG rule). */}
      <span className="co-agent-sigil flex h-8 w-8 items-center justify-center" aria-hidden="true">
        <Bot className="h-4 w-4" strokeWidth={1.8} />
      </span>

      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-medium text-foreground">{proposal.verb}</span>
          <span className="truncate text-muted-foreground">— {proposal.detail}</span>
          {proposal.flag === "cross-tenant" ? (
            <span className="shrink-0 rounded-[var(--radius-sm)] border border-warning/40 bg-warning/10 px-1.5 py-0.5 font-mono text-[10px] text-[oklch(var(--co-amber-300))]">
              cross-tenant
            </span>
          ) : null}
          {proposal.flag === "hipaa" ? (
            <span className="shrink-0 rounded-[var(--radius-sm)] border border-destructive/40 bg-destructive/10 px-1.5 py-0.5 font-mono text-[10px] text-[oklch(var(--co-rose-300))]">
              HIPAA
            </span>
          ) : null}
        </div>
        {/* Agent attribution — clearly "an agent proposed this, at this time". */}
        <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
          <span className="text-[oklch(var(--co-agent-color))]">{proposal.agentLabel}</span>
          {" · proposed "}
          {proposal.when}
        </p>
      </div>

      <ConfidenceMeterV2 value={proposal.confidence} />

      {/* Approve / reject — visible affordances (not hover-only) so the action is
          always discoverable; they brighten on row hover/focus. */}
      <span className="ml-1 flex items-center gap-1.5">
        <button
          type="button"
          className="focus-ring inline-flex h-8 items-center gap-1.5 rounded-md border border-[oklch(var(--co-emerald-500)/0.4)] bg-[oklch(var(--co-emerald-500)/0.1)] px-2.5 text-xs font-medium text-[oklch(var(--co-emerald-300))] transition-colors hover:bg-[oklch(var(--co-emerald-500)/0.2)]"
          aria-label={`Approve: ${proposal.verb}`}
        >
          <Check className="h-3.5 w-3.5" strokeWidth={2.2} />
          Approve
        </button>
        <button
          type="button"
          className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:border-destructive/50 hover:bg-destructive/10 hover:text-[oklch(var(--co-rose-300))]"
          aria-label={`Reject: ${proposal.verb}`}
        >
          <X className="h-3.5 w-3.5" strokeWidth={2.2} />
        </button>
      </span>
    </div>
  );
}

/* ------------------------------------- 5. First-class Ask AI / Analyst entry */

function AskAiEntry() {
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      {/* The problem (today) vs. the fix (v2) */}
      <div className="rounded-[var(--radius-lg)] border border-border bg-card p-6 shadow-[var(--shadow-1)]">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Today — hard to find</p>
        <div className="mt-4 flex items-center gap-3 rounded-[var(--radius-md)] border border-border bg-background px-3 py-2">
          <Search className="h-4 w-4 text-muted-foreground" strokeWidth={1.8} />
          <span className="text-sm text-muted-foreground">top bar…</span>
          <span className="ml-auto flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground" title="AI Contacts Analyst">
            <span className="bg-gradient-brand flex h-5 w-5 items-center justify-center rounded-md text-primary-foreground">
              <Sparkles className="h-3 w-3" strokeWidth={2} />
            </span>
          </span>
        </div>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          The Analyst lives behind a 20px unlabeled icon and a <kbd className="rounded border border-border bg-muted px-1 font-mono text-[11px]">⌘J</kbd> nobody
          discovers. The single most powerful surface in the product is effectively hidden.
        </p>
      </div>

      <div className="co-v2-glass co-v2-glass-edge relative overflow-hidden p-6">
        <span className="co-v2-bloom co-v2-bloom-pulse" style={{ inset: "-30% -10% auto auto", height: "70%", width: "55%" }} aria-hidden="true" />
        <div className="co-v2-stage-content">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[oklch(var(--co-fuchsia-300))]">v2 — obvious + inviting</p>

          {/* Labeled pill button (lives in the top bar / page header) */}
          <div className="mt-4">
            <p className="mb-2 text-[11px] text-muted-foreground">Labeled pill (top bar / header)</p>
            <button
              type="button"
              className="co-v2-hover-bloom co-v2-glow-strong focus-ring bg-gradient-brand inline-flex h-10 items-center gap-2 rounded-full px-4 text-sm font-semibold text-primary-foreground"
            >
              <Sparkles className="h-4 w-4" strokeWidth={2} />
              Ask AI
              <kbd className="ml-1 inline-flex items-center gap-0.5 rounded bg-black/20 px-1.5 py-0.5 font-mono text-[11px] text-primary-foreground/90">
                <Command className="h-3 w-3" strokeWidth={2} />J
              </kbd>
            </button>
          </div>

          {/* Docked launcher (bottom-right floating) */}
          <div className="mt-6">
            <p className="mb-2 text-[11px] text-muted-foreground">Docked launcher (floating, bottom-right)</p>
            <div className="relative h-24 rounded-[var(--radius-md)] border border-dashed border-[oklch(var(--co-v2-glass-hairline-strong))]">
              <button
                type="button"
                className="co-v2-hover-bloom co-v2-glass-calm focus-ring absolute bottom-3 right-3 inline-flex items-center gap-2.5 rounded-full py-2 pl-2.5 pr-4"
              >
                <span className="co-v2-glow-strong bg-gradient-brand flex h-8 w-8 items-center justify-center rounded-full text-primary-foreground">
                  <Wand2 className="h-4 w-4" strokeWidth={2} />
                </span>
                <span className="text-left">
                  <span className="block text-[13px] font-semibold leading-tight text-foreground">Ask the Analyst</span>
                  <span className="block text-[11px] leading-tight text-muted-foreground">Find anyone, summarize, dedupe…</span>
                </span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------- 6. Component strip */

function ComponentStrip() {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {/* Glass card */}
      <div className="co-v2-glass co-v2-glass-edge relative overflow-hidden p-5">
        <span className="co-v2-bloom co-v2-bloom-pulse" style={{ inset: "-40% auto auto -20%", height: "70%", width: "70%" }} aria-hidden="true" />
        <div className="co-v2-stage-content">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Layered glass</p>
          <p className="mt-2 text-sm text-foreground">Translucent fill, hairline edge, real layered shadow, backdrop blur.</p>
        </div>
      </div>

      {/* Glow button */}
      <div className="rounded-[var(--radius-lg)] border border-border bg-card p-5 shadow-[var(--shadow-1)]">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Glow button (hover blooms)</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="gradient" className="co-v2-hover-bloom co-v2-glow-strong">
            <Sparkles className="h-4 w-4" strokeWidth={2} />
            Primary action
          </Button>
        </div>
      </div>

      {/* Brand gradient text */}
      <div className="rounded-[var(--radius-lg)] border border-border bg-card p-5 shadow-[var(--shadow-1)]">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Gradient wordmark</p>
        <p className="co-v2-wordmark co-v2-wordmark-animate mt-2 text-2xl font-bold tracking-tight">Contact-Ops</p>
      </div>

      {/* Particle field sample */}
      <div className="relative h-40 overflow-hidden rounded-[var(--radius-lg)] border border-border md:col-span-2">
        <div className="co-v2-ambient absolute inset-0">
          <AmbientGraph nodeCount={22} />
        </div>
        <span className="absolute bottom-3 left-4 z-10 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Particle + relationship-graph field
        </span>
      </div>

      {/* Confidence indicators */}
      <div className="rounded-[var(--radius-lg)] border border-border bg-card p-5 shadow-[var(--shadow-1)]">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Confidence (calm)</p>
        <div className="mt-3 flex flex-col items-start gap-2">
          <ConfidenceMeterV2 value={0.96} />
          <ConfidenceMeterV2 value={0.82} />
          <ConfidenceMeterV2 value={0.61} />
          <ConfidenceMeterV2 value={0.44} />
        </div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- Page */

export function ShowcaseV2() {
  const [theme, setTheme] = useState<ThemeMode>(() => {
    const requested = new URLSearchParams(window.location.search).get("theme");
    return requested === "light" || requested === "dark" ? requested : getThemePreference();
  });

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
    <main className="min-h-screen bg-background p-4 text-foreground lg:p-8">
      <div className="mx-auto max-w-[1500px] space-y-10">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-5">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Contact-Ops</p>
            <h1 className="text-2xl font-semibold tracking-tight">
              Design Language <span className="co-v2-wordmark">v2</span> — direction showcase
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              Proposed near-future visual direction. Cinematic on brand/command surfaces, calm + fast on
              dense data. Nothing here ships to the app — for approval before any refactor.
            </p>
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
        </header>

        {/* 1. Login hero */}
        <section>
          <SectionHeading
            index={1}
            intensity="cinematic"
            title="Login hero"
            blurb="Ambient glowing relationship graph + bloom + gradient wordmark behind a layered-glass sign-in card."
          />
          <LoginHero />
        </section>

        <div className="co-v2-divider" />

        {/* 2. Dashboard command center */}
        <section>
          <SectionHeading
            index={2}
            intensity="cinematic"
            title="Dashboard command-center hero"
            blurb="Animated stat count-ups over an ambient graph backdrop. The command surface — confident, alive."
          />
          <DashboardHero />
        </section>

        <div className="co-v2-divider" />

        {/* 3. PersonCard */}
        <section>
          <SectionHeading
            index={3}
            intensity="calm"
            title="PersonCard — curated / calm"
            blurb="Glass-calm: tasteful depth with zero readability cost. AA-legible, scannable, no bloom."
          />
          <div className="grid gap-3 lg:grid-cols-2">
            {demoPeople.map((p) => (
              <PersonCardV2 key={p.name} person={p} />
            ))}
          </div>
        </section>

        <div className="co-v2-divider" />

        {/* 4. Review-queue proposal row */}
        <section>
          <SectionHeading
            index={4}
            intensity="calm"
            title="Review-queue proposal row — new calm style"
            blurb="Agent attribution + calm confidence + always-visible approve/reject. Scannable, low-noise — previews the Review Queue redesign."
          />
          <div className="rounded-[var(--radius-lg)] border border-border bg-card/60 p-2 shadow-[var(--shadow-1)]">
            <div className="space-y-1">
              {demoProposals.map((p, i) => (
                <ProposalRowV2 key={p.id} proposal={p} focused={i === 0} />
              ))}
            </div>
          </div>
        </section>

        <div className="co-v2-divider" />

        {/* 5. Ask AI entry */}
        <section>
          <SectionHeading
            index={5}
            intensity="neutral"
            title="First-class Ask AI / Analyst entry"
            blurb="Today it's a tiny unlabeled Sparkles + ⌘J nobody finds. v2 makes it obvious, labeled, and inviting."
          />
          <AskAiEntry />
        </section>

        <div className="co-v2-divider" />

        {/* 6. Component strip */}
        <section>
          <SectionHeading
            index={6}
            intensity="neutral"
            title="Component strip"
            blurb="The reusable v2 primitives: layered glass, glow button, gradient wordmark, particle field, calm confidence."
          />
          <ComponentStrip />
        </section>

        <footer className="border-t border-border py-6 text-center text-xs text-muted-foreground">
          Contact-Ops · Design Language v2 · showcase-only · tokens-v2.css is additive (tokens.css untouched)
        </footer>
      </div>
    </main>
  );
}

export default ShowcaseV2;
