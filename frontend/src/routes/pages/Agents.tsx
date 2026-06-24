/**
 * Agent Command Center (/agents), the admin console over CO's agent-governance
 * backend (mcp/tools/agent_admin.py), which until now had no frontend consumer.
 *
 * Three sections:
 *   1. Workspace kill-switch: pause / resume the whole agent fleet
 *      (get_agent_governance / set_agents_paused), confirm-gated on pause.
 *   2. Agent roster: one card per registered agent (list_agents) with its
 *      calibration tier (get_agent_trust, per agent) and per-agent controls,
 *      promote / demote tier and pause / resume the agent.
 *   3. Activity feed: recent agent-attributed action_events, reusing the
 *      dashboard's AuditList / ActionEventRow mechanism.
 *
 * Authority is enforced SERVER-SIDE (ADMIN role + "contactops:agents.admin").
 * A non-admin caller's governance read fails with a role error, and we render an
 * admin-only notice instead of the controls (same approach as Settings -> Members).
 */
import { useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownCircle,
  ArrowUpCircle,
  Bot,
  CircuitBoard,
  Pause,
  Play,
  ShieldCheck,
  ShieldAlert,
  Sparkles
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { AgentBadge } from "@/design-system/AgentBadge";
import { AuditList } from "@/routes";
import { useRecentEvents } from "@/hooks/useMcp";
import {
  isRoleDenied,
  useAgentGovernance,
  useAgentTrust,
  useAgents,
  useDemoteAgentMutation,
  usePauseAgentMutation,
  usePromoteAgentMutation,
  useResumeAgentMutation,
  useSetAgentsPausedMutation
} from "@/hooks/useAgents";
import type { AgentSummary } from "@/lib/types";
import { cn, compactDate } from "@/lib/utils";

// Trust tier (0..4) -> tokenized accent. T0/T1 never auto-apply (calm/neutral),
// T2..T4 progressively earn autonomy (sky -> emerald). The label stays
// text-foreground on the tinted pill so it always clears AA; the tier's meaning
// is carried by the dot + tint, never by a low-contrast text color.
const tierAccent: Record<number, { ring: string; dot: string }> = {
  0: { ring: "border-border bg-muted", dot: "bg-muted-foreground" },
  1: { ring: "border-info/40 bg-info/10", dot: "bg-info" },
  2: { ring: "border-info/40 bg-info/10", dot: "bg-info" },
  3: { ring: "border-success/40 bg-success/10", dot: "bg-success" },
  4: { ring: "border-primary/40 bg-primary/10", dot: "bg-primary" }
};

function TierChip({ tier, label }: { tier: number; label: string }) {
  const accent = tierAccent[tier] ?? tierAccent[0];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium text-foreground",
        accent.ring
      )}
      title={`Trust tier: ${label}`}
    >
      <span aria-hidden className={cn("h-1.5 w-1.5 shrink-0 rounded-full", accent.dot)} />
      {label}
    </span>
  );
}

// Drift status -> badge variant. "stable" reads calm (success), "warning" amber,
// "drift" is the loud one (warning text + alert glyph). Anything unknown renders
// neutral.
function DriftBadge({ status }: { status: string }) {
  if (status === "drift") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-destructive/50 bg-destructive/10 px-2 py-0.5 text-xs font-medium text-foreground">
        <AlertTriangle className="h-3 w-3 text-destructive" strokeWidth={1.8} />
        Drift detected
      </span>
    );
  }
  if (status === "warning") {
    return (
      <Badge variant="warning" className="capitalize">
        Drift warning
      </Badge>
    );
  }
  if (status === "stable") {
    return (
      <Badge variant="success" className="capitalize">
        Stable
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="capitalize">
      {status}
    </Badge>
  );
}

/**
 * A small reason-collecting dialog reused by every governed action (pause /
 * resume fleet, promote / demote, pause / resume an agent). The reason is
 * optional in the UI but the backend requires a non-empty string, so a blank
 * reason falls back to `defaultReason` on submit. Destructive actions (pausing)
 * render the confirm button in the destructive style; this is the "small
 * confirm" gate, NOT a typed-phrase gate.
 */
function ReasonDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  defaultReason,
  destructive = false,
  pending = false,
  onConfirm
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  defaultReason: string;
  destructive?: boolean;
  pending?: boolean;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");

  // Reset the field whenever the dialog re-opens so a prior reason never leaks
  // into the next action.
  const handleOpenChange = (next: boolean) => {
    if (!next) setReason("");
    onOpenChange(next);
  };

  const submit = () => {
    const trimmed = reason.trim();
    onConfirm(trimmed.length > 0 ? trimmed : defaultReason);
    setReason("");
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <label htmlFor="agent-action-reason" className="block text-sm font-medium text-foreground">
            Reason <span className="font-normal text-muted-foreground">(optional, recorded in the audit log)</span>
          </label>
          <Input
            id="agent-action-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={defaultReason}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && !pending) submit();
            }}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => handleOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            onClick={submit}
            disabled={pending}
          >
            {pending ? "Working…" : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The fleet kill-switch banner. Calm "Agents active" state with a Pause control
 * when running; a loud "Agents paused" state with the reason / who / when and a
 * Resume control when paused. A non-admin sees an admin-only notice.
 */
function KillSwitchBanner() {
  const governance = useAgentGovernance();
  const setPaused = useSetAgentsPausedMutation();
  const [pauseOpen, setPauseOpen] = useState(false);
  const [resumeOpen, setResumeOpen] = useState(false);

  if (governance.isLoading) {
    return <Skeleton className="h-28 w-full rounded-lg" />;
  }

  if (isRoleDenied(governance.error)) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
          <ShieldCheck className="h-8 w-8" strokeWidth={1.5} />
          <p className="text-sm">Only workspace admins can govern agents.</p>
          <p className="text-xs">Ask an admin of this workspace to pause, resume, or recalibrate the fleet.</p>
        </CardContent>
      </Card>
    );
  }

  if (governance.error) {
    return (
      <Card className="border-destructive/30">
        <CardContent className="flex items-center gap-3 py-5 text-sm text-muted-foreground">
          <AlertTriangle className="h-5 w-5 shrink-0 text-destructive" strokeWidth={1.8} />
          The fleet status could not be loaded. Refresh to try again.
        </CardContent>
      </Card>
    );
  }

  const data = governance.data;
  const paused = data?.agents_paused === true;

  return (
    <>
      <Card
        className={cn(
          paused ? "border-destructive/40 bg-destructive/[0.04]" : "border-success/40 bg-success/[0.04]"
        )}
      >
        <CardContent className="flex flex-col gap-4 py-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span
              className={cn(
                "flex h-10 w-10 shrink-0 items-center justify-center rounded-full",
                paused ? "bg-destructive/12 text-destructive" : "bg-success/12 text-success"
              )}
            >
              {paused ? (
                <ShieldAlert className="h-5 w-5" strokeWidth={1.8} />
              ) : (
                <ShieldCheck className="h-5 w-5" strokeWidth={1.8} />
              )}
            </span>
            <div className="min-w-0">
              <p className="text-base font-semibold text-foreground">
                {paused ? "Agents paused" : "Agents active"}
              </p>
              {paused ? (
                <div className="mt-0.5 space-y-0.5 text-sm text-muted-foreground">
                  <p>The whole fleet is held. No agent will act until you resume.</p>
                  {data?.agents_paused_reason ? (
                    <p>
                      Reason: <span className="text-foreground">{data.agents_paused_reason}</span>
                    </p>
                  ) : null}
                  <p className="co-mono-numeric text-xs">
                    {data?.agents_paused_by ? `Paused by ${data.agents_paused_by}` : "Paused"}
                    {data?.agents_paused_at ? ` · ${compactDate(data.agents_paused_at)}` : ""}
                  </p>
                </div>
              ) : (
                <p className="mt-0.5 text-sm text-muted-foreground">
                  Agents are running normally and may propose or apply changes within their trust tier.
                </p>
              )}
            </div>
          </div>
          <div className="shrink-0">
            {paused ? (
              <Button variant="default" onClick={() => setResumeOpen(true)} disabled={setPaused.isPending}>
                <Play className="h-4 w-4" strokeWidth={1.8} />
                Resume agents
              </Button>
            ) : (
              <Button
                variant="outline"
                className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
                onClick={() => setPauseOpen(true)}
                disabled={setPaused.isPending}
              >
                <Pause className="h-4 w-4" strokeWidth={1.8} />
                Pause all agents
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <ReasonDialog
        open={pauseOpen}
        onOpenChange={setPauseOpen}
        title="Pause the whole agent fleet?"
        description="Every agent stops acting in this workspace until you resume. Proposals already in the review queue are unaffected."
        confirmLabel="Pause all agents"
        defaultReason="Paused by an administrator"
        destructive
        pending={setPaused.isPending}
        onConfirm={(reason) =>
          void setPaused.mutateAsync({ paused: true, reason }).then(() => setPauseOpen(false)).catch(() => {})
        }
      />
      <ReasonDialog
        open={resumeOpen}
        onOpenChange={setResumeOpen}
        title="Resume the agent fleet?"
        description="Agents will start acting again within their trust tier."
        confirmLabel="Resume agents"
        defaultReason="Resumed by an administrator"
        pending={setPaused.isPending}
        onConfirm={(reason) =>
          void setPaused.mutateAsync({ paused: false, reason }).then(() => setResumeOpen(false)).catch(() => {})
        }
      />
    </>
  );
}

/** Calibration line for one roster card: tier + drift + Beta mean, or a calm
 * "not yet calibrated" fallback that still shows the agent's initial tier. */
function AgentTrustLine({ agent }: { agent: AgentSummary }) {
  const trust = useAgentTrust(agent.slug, "private");

  if (trust.isLoading) {
    return <Skeleton className="h-5 w-44" />;
  }

  // A role denial is handled once at the page level; here we degrade to the
  // initial tier rather than spew an error per card.
  const result = trust.data;
  const row = result?.found ? result.trust : null;

  if (!row) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <TierChip tier={agent.initial_trust_tier} label={agent.initial_trust_tier_label} />
        <span className="text-xs text-muted-foreground">Not yet calibrated</span>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <TierChip tier={row.stored_tier} label={row.stored_tier_label} />
      <DriftBadge status={row.drift_status} />
      <span className="co-mono-numeric text-xs text-muted-foreground" title="Beta posterior mean approval rate">
        {(row.mean * 100).toFixed(1)}% mean · {row.samples_total.toLocaleString()} samples
      </span>
    </div>
  );
}

type PendingAction =
  | { kind: "promote" | "demote"; agent: AgentSummary }
  | { kind: "pause" | "resume"; agent: AgentSummary }
  | null;

function AgentCard({
  agent,
  onAction,
  busy
}: {
  agent: AgentSummary;
  onAction: (action: NonNullable<PendingAction>) => void;
  busy: boolean;
}) {
  const budget =
    agent.cost_budget_monthly_cents > 0
      ? `$${(agent.cost_budget_monthly_cents / 100).toLocaleString(undefined, {
          minimumFractionDigits: 0,
          maximumFractionDigits: 2
        })}/mo budget`
      : null;

  return (
    <Card className="flex flex-col transition-colors hover:border-primary/30">
      <CardHeader className="space-y-2">
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="flex min-w-0 items-center gap-2 text-base">
            <AgentBadge slug={agent.slug} label={agent.name} size="sm" showLabel={false} />
            <span className="truncate">{agent.name}</span>
          </CardTitle>
          <Badge variant="outline" className="shrink-0 capitalize">
            {agent.agent_class.replace(/_/g, " ")}
          </Badge>
        </div>
        <CardDescription className="line-clamp-2">{agent.description}</CardDescription>
        <AgentTrustLine agent={agent} />
      </CardHeader>
      <CardContent className="mt-auto space-y-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="co-mono-numeric">v{agent.version}</span>
          {budget ? (
            <>
              <span aria-hidden className="text-border">
                ·
              </span>
              <span className="co-mono-numeric">{budget}</span>
            </>
          ) : null}
          {agent.declared_capabilities.length ? (
            <>
              <span aria-hidden className="text-border">
                ·
              </span>
              <span>
                {agent.declared_capabilities.length}{" "}
                {agent.declared_capabilities.length === 1 ? "capability" : "capabilities"}
              </span>
            </>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onAction({ kind: "promote", agent })}
          >
            <ArrowUpCircle className="h-4 w-4 text-success" strokeWidth={1.8} />
            Promote
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onAction({ kind: "demote", agent })}
          >
            <ArrowDownCircle className="h-4 w-4 text-warning" strokeWidth={1.8} />
            Demote
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="ml-auto border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
            disabled={busy}
            onClick={() => onAction({ kind: "pause", agent })}
          >
            <Pause className="h-4 w-4" strokeWidth={1.8} />
            Pause
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => onAction({ kind: "resume", agent })}
          >
            <Play className="h-4 w-4" strokeWidth={1.8} />
            Resume
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function AgentRoster() {
  const agents = useAgents();
  const promote = usePromoteAgentMutation();
  const demote = useDemoteAgentMutation();
  const pause = usePauseAgentMutation();
  const resume = useResumeAgentMutation();

  const [pending, setPending] = useState<PendingAction>(null);

  const items = useMemo(() => agents.data?.agents ?? [], [agents.data]);
  const busy = promote.isPending || demote.isPending || pause.isPending || resume.isPending;

  // A role denial on the roster read is the same admin-only situation as the
  // banner; the banner already shows the notice, so here we simply render nothing
  // rather than a second copy.
  if (isRoleDenied(agents.error)) {
    return null;
  }

  // Dialog copy + handler resolved from the pending action.
  const dialogProps = (() => {
    if (!pending) return null;
    const a = pending.agent;
    switch (pending.kind) {
      case "promote":
        return {
          title: `Promote ${a.name}?`,
          description: "Moves this agent's stored trust tier up by one. Higher tiers can auto-apply more.",
          confirmLabel: "Promote agent",
          defaultReason: "Manual promotion by an administrator",
          destructive: false,
          pending: promote.isPending,
          onConfirm: (reason: string) =>
            void promote
              .mutateAsync({ agent_slug: a.slug, reason })
              .then(() => setPending(null))
              .catch(() => {})
        };
      case "demote":
        return {
          title: `Demote ${a.name}?`,
          description: "Moves this agent's stored trust tier down by one, reducing what it may auto-apply.",
          confirmLabel: "Demote agent",
          defaultReason: "Manual demotion by an administrator",
          destructive: false,
          pending: demote.isPending,
          onConfirm: (reason: string) =>
            void demote
              .mutateAsync({ agent_slug: a.slug, reason })
              .then(() => setPending(null))
              .catch(() => {})
        };
      case "pause":
        return {
          title: `Pause ${a.name}?`,
          description: "Opens this agent's circuit breaker. It refuses to run until you resume it.",
          confirmLabel: "Pause agent",
          defaultReason: "Paused by an administrator",
          destructive: true,
          pending: pause.isPending,
          onConfirm: (reason: string) =>
            void pause
              .mutateAsync({ agent_slug: a.slug, reason })
              .then(() => setPending(null))
              .catch(() => {})
        };
      case "resume":
        return {
          title: `Resume ${a.name}?`,
          description: "Closes this agent's circuit breaker so it can run again.",
          confirmLabel: "Resume agent",
          defaultReason: "Resumed by an administrator",
          destructive: false,
          pending: resume.isPending,
          onConfirm: (reason: string) =>
            void resume
              .mutateAsync({ agent_slug: a.slug, reason })
              .then(() => setPending(null))
              .catch(() => {})
        };
    }
  })();

  return (
    <section className="space-y-4">
      <div className="flex items-center gap-2.5">
        <Bot className="h-5 w-5 text-[oklch(var(--co-sky-500))]" strokeWidth={1.8} />
        <h2 className="text-lg font-semibold tracking-tight">Agent roster</h2>
        {!agents.isLoading && agents.data ? (
          <span className="co-mono-numeric rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
            {items.length}
          </span>
        ) : null}
      </div>

      {agents.isLoading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardHeader className="space-y-2">
                <Skeleton className="h-5 w-40" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-5 w-44" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-8 w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : items.length ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {items.map((agent) => (
            <AgentCard key={agent.slug} agent={agent} onAction={setPending} busy={busy} />
          ))}
        </div>
      ) : (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Sparkles className="h-6 w-6" strokeWidth={1.5} />
            </div>
            <div className="space-y-1">
              <h3 className="text-lg font-semibold">No agents registered</h3>
              <p className="mx-auto max-w-md text-sm text-muted-foreground">
                Agents from the fleet runtime appear here with their trust tier and controls once registered.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {dialogProps ? (
        <ReasonDialog open={pending !== null} onOpenChange={(next) => (next ? null : setPending(null))} {...dialogProps} />
      ) : null}
    </section>
  );
}

function AgentActivityFeed() {
  const events = useRecentEvents();
  const eventItems = events.data?.items ?? [];

  return (
    <section className="space-y-4">
      <div className="flex items-center gap-2.5">
        <Activity className="h-5 w-5 text-muted-foreground" strokeWidth={1.8} />
        <h2 className="text-lg font-semibold tracking-tight">Agent activity</h2>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <CircuitBoard className="h-4 w-4 text-[oklch(var(--co-fuchsia-500))]" strokeWidth={1.8} />
            Recent actions
          </CardTitle>
          <CardDescription>Edits, merges, and agent decisions recorded across this workspace.</CardDescription>
        </CardHeader>
        <CardContent>
          {events.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full rounded-md" />
              ))}
            </div>
          ) : eventItems.length ? (
            <AuditList events={eventItems} />
          ) : (
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              <Activity className="h-8 w-8 text-muted-foreground" strokeWidth={1.5} />
              <p className="text-sm font-medium">No activity yet</p>
              <p className="max-w-xs text-xs text-muted-foreground">
                Agent actions and human decisions across this workspace will show up here.
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

export function AgentsRoute() {
  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center gap-2.5">
          <span className="inline-flex h-6 items-center rounded-full border border-primary/30 bg-primary/10 px-2 text-[11px] font-semibold uppercase tracking-wider text-primary">
            Admin
          </span>
          <h1 className="text-2xl font-semibold tracking-tight">Agent Command Center</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Govern the agent fleet: pause or resume everything at once, calibrate each agent's trust tier, and watch what
          they do.
        </p>
      </div>

      <KillSwitchBanner />
      <AgentRoster />
      <AgentActivityFeed />
    </div>
  );
}
