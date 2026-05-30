/**
 * PATPreview — Showcase tile for the Personal Access Tokens settings card.
 *
 * Builds a presentational mirror of `PersonalAccessTokensSection` using
 * local state instead of React Query, so the Showcase can render the
 * generate form, the amber issuance callout, the populated list, and the
 * "How do I use this?" disclosure without a live MCP backend.
 *
 * If the real component changes, this preview is allowed to lag — it's
 * a fixture, not the source of truth for production behavior.
 */
import { useEffect, useMemo, useState } from "react";
import { applyThemePreference, type ThemeMode } from "@/design-system/tokens";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Key,
  Plus,
  Terminal,
  Trash2
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { PersonalAccessToken } from "@/lib/types";
import { cn, compactDate } from "@/lib/utils";

type ExpiryOption = { label: string; days: number | null };
const EXPIRY_OPTIONS: ExpiryOption[] = [
  { label: "Never", days: null },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 }
];

const SAMPLE_SCOPES = [
  "person:read",
  "person:write",
  "org:read",
  "org:write",
  "tag:read",
  "tag:write"
];

function daysFromNow(d: number): string {
  return new Date(Date.now() + d * 86400 * 1000).toISOString();
}
function daysAgo(d: number): string {
  return new Date(Date.now() - d * 86400 * 1000).toISOString();
}

const SEED_TOKENS: PersonalAccessToken[] = [
  {
    id: "pat-1",
    display_name: "Claude Code on Mac Studio",
    last_4: "9f2a",
    scopes: ["person:read", "person:write", "org:read"],
    expires_at: null,
    created_at: daysAgo(14),
    last_used_at: daysAgo(0)
  },
  {
    id: "pat-2",
    display_name: "Cline on Strix Halo",
    last_4: "1c87",
    scopes: ["person:read", "org:read"],
    expires_at: daysFromNow(6),
    created_at: daysAgo(60),
    last_used_at: daysAgo(2)
  },
  {
    id: "pat-3",
    display_name: "Cursor (laptop)",
    last_4: "4e10",
    scopes: ["person:read"],
    expires_at: daysFromNow(180),
    created_at: daysAgo(3),
    last_used_at: null
  }
];

function relTime(iso: string | null): string {
  if (!iso) return "Never";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "—";
  const diff = Date.now() - ts;
  const sec = Math.round(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day}d ago`;
  return compactDate(iso);
}

function expiresLabel(iso: string | null): string {
  if (!iso) return "Never expires";
  const diff = new Date(iso).getTime() - Date.now();
  if (diff <= 0) return "Expired";
  const day = Math.round(diff / (1000 * 60 * 60 * 24));
  if (day < 30) return `Expires in ${day}d`;
  if (day < 365) return `Expires in ${Math.round(day / 30)}mo`;
  return `Expires ${compactDate(iso)}`;
}

function expiryTone(iso: string | null): "ok" | "soon" | "expired" {
  if (!iso) return "ok";
  const diff = new Date(iso).getTime() - Date.now();
  if (diff <= 0) return "expired";
  if (diff / 86400000 < 14) return "soon";
  return "ok";
}

type Scenario = "empty" | "issued" | "populated" | "help";

function PATCard({ scenario }: { scenario: Scenario }) {
  const [formOpen, setFormOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(scenario === "help");
  const [name, setName] = useState(scenario === "issued" ? "Claude Code on my Mac" : "");
  const [expiryDays, setExpiryDays] = useState<number | null>(null);
  const [scopes, setScopes] = useState<string[]>([]);
  const showIssued = scenario === "issued";
  const items: PersonalAccessToken[] = scenario === "populated" || scenario === "help" ? SEED_TOKENS : [];

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2 text-base">
            <Terminal className="h-4 w-4 text-[oklch(var(--co-sky-500))]" strokeWidth={1.8} />
            Personal Access Tokens
          </CardTitle>
          <CardDescription>
            For native MCP access from AI clients like Claude Code, Cline, or Cursor. Each token
            can be revoked individually.
          </CardDescription>
        </div>
        <Button size="sm" disabled={formOpen} onClick={() => setFormOpen(true)}>
          <Plus className="h-4 w-4" strokeWidth={1.6} />
          Generate token
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {formOpen ? (
          <div className="space-y-3 rounded-md border border-border bg-muted/30 p-3">
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-foreground">Display name</label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Claude Code on my Mac"
              />
            </div>
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-foreground">Expires in</label>
              <div className="flex flex-wrap gap-1.5">
                {EXPIRY_OPTIONS.map((opt) => {
                  const active = expiryDays === opt.days;
                  return (
                    <button
                      key={opt.label}
                      type="button"
                      onClick={() => setExpiryDays(opt.days)}
                      className={cn(
                        "focus-ring rounded-full border px-2.5 py-1 text-xs font-medium",
                        active
                          ? "border-primary/60 bg-primary/15 text-link"
                          : "border-border bg-background text-muted-foreground hover:bg-muted"
                      )}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-foreground">
                Scopes <span className="font-normal text-muted-foreground">(optional)</span>
              </label>
              <div className="flex flex-wrap gap-1.5">
                {SAMPLE_SCOPES.map((scope) => {
                  const active = scopes.includes(scope);
                  return (
                    <button
                      key={scope}
                      type="button"
                      onClick={() =>
                        setScopes((prev) =>
                          prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
                        )
                      }
                      className={cn(
                        "focus-ring rounded-full border px-2.5 py-1 font-mono text-[11px] font-medium",
                        active
                          ? "border-primary/60 bg-primary/15 text-link"
                          : "border-border bg-background text-muted-foreground hover:bg-muted"
                      )}
                    >
                      {scope}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="flex items-center justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setFormOpen(false)}>
                Cancel
              </Button>
              <Button size="sm" variant="gradient">
                Generate
              </Button>
            </div>
          </div>
        ) : null}

        {showIssued ? (
          <div className="rounded-md border border-[oklch(var(--co-amber-500)/0.4)] bg-[oklch(var(--co-amber-500)/0.08)] p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 space-y-1.5">
                <p className="text-sm font-semibold">New personal access token generated</p>
                <p className="text-xs text-muted-foreground">
                  For <span className="font-medium">Claude Code on my Mac</span>. This is the only
                  time the full token will be shown. Copy it now.
                </p>
                <code className="block w-full break-all rounded bg-background/80 px-2 py-1.5 font-mono text-xs">
                  co_pat_M73vK9p2qLwR8eXh3sZcN4yT5bAfDgJ6uVw1xCqM8nKt
                </code>
              </div>
              <div className="flex shrink-0 flex-col gap-1">
                <Button variant="ghost" size="icon" aria-label="Copy token">
                  <CheckCircle2 className="h-4 w-4 text-success" strokeWidth={1.6} />
                </Button>
                <Button variant="ghost" size="sm">
                  Done
                </Button>
              </div>
            </div>
          </div>
        ) : null}

        {items.length > 0 ? (
          <ul className="space-y-2" aria-label="Personal access tokens">
            {items.map((pat) => {
              const tone = expiryTone(pat.expires_at);
              return (
                <li
                  key={pat.id}
                  className="rounded-md border border-border p-3 transition-colors hover:bg-muted/50"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="truncate font-medium text-foreground">{pat.display_name}</p>
                        <code className="font-mono text-xs text-muted-foreground">
                          co_pat_…{pat.last_4}
                        </code>
                      </div>
                      {pat.scopes.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {pat.scopes.map((scope) => (
                            <Badge
                              key={scope}
                              variant="outline"
                              className="rounded font-mono text-[10px]"
                            >
                              {scope}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <p className="text-xs text-muted-foreground">Inherits all scopes</p>
                      )}
                      <p className="co-mono-numeric text-xs text-muted-foreground">
                        Created {relTime(pat.created_at)}
                        {" · "}
                        Last used {relTime(pat.last_used_at)}
                        {" · "}
                        <span
                          className={cn(
                            tone === "soon" && "text-warning",
                            tone === "expired" && "text-destructive"
                          )}
                        >
                          {expiresLabel(pat.expires_at)}
                        </span>
                      </p>
                    </div>
                    <Button variant="ghost" size="icon" aria-label="Revoke token">
                      <Trash2 className="h-4 w-4 text-destructive" strokeWidth={1.6} />
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : !showIssued ? (
          <div className="flex flex-col items-center gap-2 py-8 text-center text-muted-foreground">
            <Key className="h-8 w-8" strokeWidth={1.5} />
            <p className="text-sm">No tokens issued yet.</p>
            <p className="text-xs">Click Generate above to create one for an AI client.</p>
          </div>
        ) : null}

        <button
          type="button"
          onClick={() => setHelpOpen((v) => !v)}
          className="focus-ring -mx-1 flex items-center gap-1 rounded px-1 py-1 text-xs font-medium text-muted-foreground hover:text-foreground"
          aria-expanded={helpOpen}
        >
          {helpOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          How do I use this?
        </button>
        {helpOpen ? (
          <ol className="ml-6 list-decimal space-y-2 text-xs text-muted-foreground">
            <li>
              Copy the generated token (it looks like{" "}
              <code className="font-mono text-foreground">co_pat_abc123…</code>).
            </li>
            <li>
              In your AI client&apos;s MCP config, add a new server with URL{" "}
              <code className="font-mono text-foreground">
                https://mcp.contacts.magicunicorn.dev/mcp
              </code>
              .
            </li>
            <li>
              Set the auth header to{" "}
              <code className="font-mono text-foreground">REMOVED…</code>.
            </li>
            <li>
              Tools like <code className="font-mono text-foreground">list_people</code>,{" "}
              <code className="font-mono text-foreground">find_person_by_identifier</code>, etc. are
              now native to your AI.
            </li>
          </ol>
        ) : null}
      </CardContent>
    </Card>
  );
}

function readUrl(): { theme: ThemeMode | null; scenario: Scenario | "all" } {
  const params = new URLSearchParams(window.location.search);
  const themeRaw = params.get("theme");
  const theme: ThemeMode | null = themeRaw === "light" || themeRaw === "dark" ? themeRaw : null;
  const scenarioRaw = params.get("scenario");
  const scenarios: Scenario[] = ["empty", "issued", "populated", "help"];
  const scenario = (scenarios as string[]).includes(scenarioRaw ?? "")
    ? (scenarioRaw as Scenario)
    : "all";
  return { theme, scenario };
}

export function PATPreview() {
  const initial = useMemo(() => readUrl(), []);
  useEffect(() => {
    if (initial.theme) applyThemePreference(initial.theme);
  }, [initial.theme]);

  if (initial.scenario !== "all") {
    return (
      <div className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
          {initial.scenario === "empty" && "Empty state"}
          {initial.scenario === "issued" && "After issuance (amber callout)"}
          {initial.scenario === "populated" && "Populated (active · expiring · never expires)"}
          {initial.scenario === "help" && '"How do I use this?" expanded'}
        </p>
        <PATCard scenario={initial.scenario} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 2xl:grid-cols-2">
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
            Empty state
          </p>
          <PATCard scenario="empty" />
        </div>
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
            After issuance (amber callout)
          </p>
          <PATCard scenario="issued" />
        </div>
      </div>
      <div className="grid gap-4 2xl:grid-cols-2">
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
            Populated (active · expiring · never expires)
          </p>
          <PATCard scenario="populated" />
        </div>
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
            &quot;How do I use this?&quot; expanded
          </p>
          <PATCard scenario="help" />
        </div>
      </div>
    </div>
  );
}
