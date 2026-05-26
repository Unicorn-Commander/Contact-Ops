# Contact-Ops Design System

Stage 0 foundation for Contact-Ops admin surfaces. The system defaults to dark mode, uses Geist Sans plus Geist Mono, and expresses color in OKLCH tokens.

## Token Reference

### Color Ramps

All ramp values are OKLCH. Use neutral for surfaces and text, brand for Contact-Ops emphasis, and emerald, sky, amber, rose for confidence states.

| Ramp | 50 | 100 | 200 | 300 | 500 | 700 | 900 | 950 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| neutral | `oklch(0.986 0.004 76)` | `oklch(0.956 0.005 76)` | `oklch(0.904 0.006 76)` | `oklch(0.806 0.007 76)` | `oklch(0.548 0.009 76)` | `oklch(0.334 0.009 76)` | `oklch(0.182 0.009 76)` | `oklch(0.138 0.009 76)` |
| brand | `oklch(0.972 0.031 318)` | `oklch(0.925 0.056 318)` | `oklch(0.841 0.096 318)` | `oklch(0.739 0.139 318)` | `oklch(0.617 0.190 318)` | `oklch(0.492 0.170 318)` | `oklch(0.355 0.120 318)` | `oklch(0.244 0.082 318)` |
| emerald | `oklch(0.966 0.035 154)` | `oklch(0.910 0.068 154)` | `oklch(0.804 0.119 154)` | `oklch(0.714 0.156 154)` | `oklch(0.634 0.175 154)` | `oklch(0.503 0.137 154)` | `oklch(0.384 0.095 154)` | `oklch(0.267 0.067 154)` |
| sky | `oklch(0.968 0.027 226)` | `oklch(0.914 0.056 226)` | `oklch(0.826 0.091 226)` | `oklch(0.728 0.126 226)` | `oklch(0.637 0.153 226)` | `oklch(0.516 0.128 226)` | `oklch(0.394 0.089 226)` | `oklch(0.285 0.062 226)` |
| amber | `oklch(0.976 0.048 84)` | `oklch(0.926 0.086 84)` | `oklch(0.842 0.126 84)` | `oklch(0.756 0.150 84)` | `oklch(0.676 0.165 84)` | `oklch(0.548 0.126 84)` | `oklch(0.410 0.086 84)` | `oklch(0.302 0.063 84)` |
| rose | `oklch(0.958 0.033 18)` | `oklch(0.896 0.065 18)` | `oklch(0.794 0.112 18)` | `oklch(0.694 0.155 18)` | `oklch(0.606 0.199 18)` | `oklch(0.506 0.174 18)` | `oklch(0.390 0.121 18)` | `oklch(0.286 0.085 18)` |

### Semantic Color Tokens

| Token | Dark value | Light value | Use |
| --- | --- | --- | --- |
| `--background` | `neutral-950` | `neutral-50` | Page background |
| `--foreground` | `oklch(0.935 0.006 76)` | `oklch(0.180 0.011 76)` | Primary text |
| `--card` | `oklch(0.168 0.010 76)` | `oklch(0.998 0.002 76)` | Cards and rows |
| `--muted` | `oklch(0.218 0.011 76)` | `oklch(0.940 0.006 76)` | Secondary surfaces |
| `--muted-foreground` | `oklch(0.686 0.009 76)` | `oklch(0.468 0.010 76)` | Secondary text |
| `--border` | `oklch(0.282 0.010 76)` | `oklch(0.875 0.008 76)` | Soft structure |
| `--ring` | `sky-500` | `sky-700` | Focus rings |
| `--confidence-emerald` | `emerald-500` | `emerald-700` | Confidence >= 0.90 |
| `--confidence-sky` | `sky-500` | `sky-700` | Confidence 0.75 to 0.89 |
| `--confidence-amber` | `amber-500` | `amber-700` | Confidence 0.50 to 0.74 |
| `--confidence-rose` | `rose-500` | `rose-700` | Confidence < 0.50 |

### Type

`--font-sans` is Geist. `--font-mono` is Geist Mono. UI text uses proportional numerals. Tables, IDs, counts, and confidence values use `MonoNumeric` or `.co-mono-numeric`.

| Token | Size | Line height | Use |
| --- | --- | --- | --- |
| `--text-11` | `0.6875rem` | `1rem` | Dense metadata |
| `--text-12` | `0.75rem` | `1rem` | Badges, hints |
| `--text-13` | `0.8125rem` | `1.125rem` | Dense rows |
| `--text-14` | `0.875rem` | `1.25rem` | Default UI |
| `--text-16` | `1rem` | `1.5rem` | Body |
| `--text-18` | `1.125rem` | `1.625rem` | Section headings |
| `--text-20` | `1.25rem` | `1.75rem` | Page headings |
| `--text-24` | `1.5rem` | `2rem` | Showcase headings |
| `--text-30` | `1.875rem` | `2.375rem` | Reserved display |
| `--text-36` | `2.25rem` | `2.75rem` | Reserved display |

### Space, Radius, Shadow, Motion

Spacing tokens are `--co-space-2`, `--co-space-4`, `--co-space-6`, `--co-space-8`, `--co-space-12`, `--co-space-16`, `--co-space-20`, `--co-space-24`, `--co-space-32`, `--co-space-40`, `--co-space-56`, `--co-space-72`.

Radius tokens are `--radius-sm: 4px`, `--radius-md: 8px`, `--radius-lg: 12px`, and `--radius-pill: 999px`.

Shadow tokens are `--shadow-1` through `--shadow-4`. Use `--shadow-1` for row lift, `--shadow-2` for panels, `--shadow-3` for drawers, and `--shadow-4` for command palette or modal depth.

Motion durations are `75`, `150`, `200`, `300`, and `450ms`. Spring presets are:

```ts
gentle: { stiffness: 170, damping: 24, mass: 1, bounce: 0.04 }
default: { stiffness: 260, damping: 30, mass: 1, bounce: 0.06 }
snappy: { stiffness: 420, damping: 34, mass: 0.9, bounce: 0.04 }
bouncy: { stiffness: 360, damping: 22, mass: 0.8, bounce: 0.1 }
```

Use `gentle` for row reveal, `default` for panels, `snappy` for hover and focus, and `bouncy` only for rare confirmation feedback. Every motion primitive honors reduced motion.

## Primitive APIs

```ts
function AgentBadge(props: {
  slug: string;
  label?: string;
  status?: "idle" | "thinking" | "waiting" | "executing" | "done" | "error";
  size?: "xs" | "sm" | "md";
  showLabel?: boolean;
  disabled?: boolean;
  className?: string;
}): JSX.Element;

function HumanBadge(props: {
  name: string;
  label?: string;
  size?: "xs" | "sm" | "md";
  showLabel?: boolean;
  disabled?: boolean;
  className?: string;
}): JSX.Element;

function ConfidenceIndicator(props: {
  value: number;
  mode?: "binary" | "numeric";
  size?: "sm" | "md";
  label?: string;
  showLabel?: boolean;
  disabled?: boolean;
  loading?: boolean;
  error?: boolean;
  className?: string;
}): JSX.Element;

function ActionAttribution(props: {
  actor: { type: "agent"; slug: string; label?: string } | { type: "human"; name: string; label?: string };
  verb: string;
  timestamp: Date | string | number;
  target?: string;
  compact?: boolean;
  disabled?: boolean;
  className?: string;
}): JSX.Element;

function KeyboardHint(props: {
  keys: string | string[];
  label?: string;
  disabled?: boolean;
  className?: string;
}): JSX.Element;

function DensityProvider(props: {
  children: React.ReactNode;
  defaultDensity?: "compact" | "default" | "comfortable";
}): JSX.Element;

function useDensity(): {
  density: "compact" | "default" | "comfortable";
  setDensity: (density: "compact" | "default" | "comfortable") => void;
};

function Spinner(props: { label?: string; size?: "sm" | "md" | "lg"; className?: string }): JSX.Element;
function Pulse(props: { agentSlug?: string; label?: string; className?: string }): JSX.Element;
function Streaming(props: { text: string; complete?: boolean; label?: string; className?: string }): JSX.Element;
function MonoNumeric(props: { children: React.ReactNode; tone?: "default" | "muted" | "strong"; className?: string; title?: string }): JSX.Element;
```

## AgentBadge vs HumanBadge

Use `AgentBadge` when the actor is a software agent, daemon, source bridge, model-backed worker, or automated reconciliation process. Use it for proposals, auto-applied events, audit rows, graph edges, evidence rows, and loading states tied to agent work.

Use `HumanBadge` when a person approved, edited, rejected, reverted, asserted, or manually created something. Human badges may use initials. Agent badges never use initials, portraits, or human-shaped avatars.

If an action has both, show both in order of authority: agent proposed, human approved.

## Density Guidance

`compact` is for triage and graph overlays. Use it for inbox rows, command palettes, audit event streams, and dense graph labels.

`default` is for curation. Use it for people, organizations, tags, tenant lists, and dashboard tables.

`comfortable` is for settings and forms where reading and precision matter more than row throughput.

## Linear AIG Compliance Checklist

- [x] Agents are visually distinct from humans.
- [x] Agent representation cannot be mistaken for a person.
- [x] Agent badges avoid human avatars, initials, and portraits.
- [x] Agent actions use the same feed and audit surfaces as human actions.
- [x] Agent state feedback is immediate and unobtrusive.
- [x] Thinking, waiting, executing, finished, and error states are representable.
- [x] Action attribution always includes actor type, actor identity, verb, and time.
- [x] Confidence uses fast binary solid versus hatched states before numeric detail.
- [x] Human authority remains explicit through `HumanBadge` and `ActionAttribution`.
- [x] Primitives leave space for inspectable reasoning, evidence, and tool-call links at the decision point.

## Contrast Verification

WCAG AA normal text threshold is 4.5:1. Confidence aliases swap by theme so the same component can be used on both backgrounds.

| Confidence alias | Dark background ratio | Light background ratio | Result |
| --- | ---: | ---: | --- |
| `--confidence-emerald` | 6.35 | 5.27 | AA |
| `--confidence-sky` | 6.32 | 5.01 | AA |
| `--confidence-amber` | 6.76 | 4.74 | AA |
| `--confidence-rose` | 4.68 | 6.17 | AA |

The indicator label text itself uses `--foreground`; color is used for the meter, border, and glyph. Numeric confidence mode uses tabular Geist Mono.
