/**
 * Personal Access Tokens — Settings card.
 *
 * Mirrors the CardDAV passwords card's shape:
 *   - Header with title + description + Generate button (top-right)
 *   - Expanded inline form when generating (NOT a modal — matches CardDAV feel)
 *   - Amber callout for the just-issued plaintext token (shown once)
 *   - List of existing tokens with masked suffix + scopes + dates + revoke
 *   - Empty state when none issued yet
 *   - "How do I use this?" collapsible disclosure at the bottom
 *
 * Backend tools called: `issue_personal_access_token`,
 * `list_personal_access_tokens`, `revoke_personal_access_token` (Codex
 * branch). All consumed via `@/lib/mcp`.
 */
import { useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  Key,
  Plus,
  Terminal,
  Trash2
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { env } from "@/lib/env";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  usePersonalAccessTokens,
  useIssuePatMutation,
  useRevokePatMutation
} from "@/hooks/usePersonalAccessTokens";
import type { PersonalAccessToken, PersonalAccessTokenIssued } from "@/lib/types";
import { cn, compactDate } from "@/lib/utils";

type ExpiryOption = { label: string; days: number | null };

const EXPIRY_OPTIONS: ExpiryOption[] = [
  { label: "Never", days: null },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 }
];

// Read-only sample scopes shown in the form. The backend ultimately gates which
// scopes are allowed for the calling user; the chips here are a hint, not
// authoritative. Selecting none = inherits all the user's existing scopes.
const SAMPLE_SCOPES = [
  "person:read",
  "person:write",
  "org:read",
  "org:write",
  "tag:read",
  "tag:write"
];

function relTime(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const ts = new Date(iso).getTime();
  const diff = Date.now() - ts;
  if (Number.isNaN(diff)) return "—";
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

function expiresLabel(iso: string | null | undefined): string {
  if (!iso) return "Never expires";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "—";
  const diff = ts - Date.now();
  if (diff <= 0) return "Expired";
  const day = Math.round(diff / (1000 * 60 * 60 * 24));
  if (day <= 1) return "Expires today";
  if (day < 30) return `Expires in ${day}d`;
  if (day < 365) return `Expires in ${Math.round(day / 30)}mo`;
  return `Expires ${compactDate(iso)}`;
}

function expiryTone(iso: string | null | undefined): "ok" | "soon" | "expired" {
  if (!iso) return "ok";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "ok";
  const diff = ts - Date.now();
  if (diff <= 0) return "expired";
  const day = diff / (1000 * 60 * 60 * 24);
  if (day < 14) return "soon";
  return "ok";
}

export function PersonalAccessTokensSection() {
  const list = usePersonalAccessTokens();
  const issue = useIssuePatMutation();
  const revoke = useRevokePatMutation();

  const [formOpen, setFormOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [expiryDays, setExpiryDays] = useState<number | null>(null);
  const [selectedScopes, setSelectedScopes] = useState<string[]>([]);
  const [newlyIssued, setNewlyIssued] = useState<PersonalAccessTokenIssued | null>(null);
  const [copied, setCopied] = useState(false);

  const items: PersonalAccessToken[] = list.data?.items ?? [];

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    );
  };

  const resetForm = () => {
    setDisplayName("");
    setExpiryDays(null);
    setSelectedScopes([]);
  };

  const handleGenerate = async () => {
    if (!displayName.trim()) return;
    try {
      const result = await issue.mutateAsync({
        display_name: displayName.trim(),
        scopes: selectedScopes.length > 0 ? selectedScopes : undefined,
        expires_in_days: expiryDays
      });
      setNewlyIssued(result);
      setFormOpen(false);
      resetForm();
    } catch {
      // toast handled in the hook
    }
  };

  const handleCopy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard may be blocked under non-HTTPS; the value is selectable.
    }
  };

  const dismissNewlyIssued = () => {
    setNewlyIssued(null);
    setCopied(false);
  };

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
        <Button
          size="sm"
          disabled={formOpen || issue.isPending}
          onClick={() => {
            setNewlyIssued(null);
            setFormOpen(true);
          }}
        >
          <Plus className="h-4 w-4" strokeWidth={1.6} />
          Generate token
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {formOpen ? (
          <div className="space-y-3 rounded-md border border-border bg-muted/30 p-3">
            <div className="space-y-1.5">
              <label
                htmlFor="pat-display-name"
                className="block text-sm font-medium text-foreground"
              >
                Display name
              </label>
              <Input
                id="pat-display-name"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Claude Code on my Mac"
                autoFocus
              />
              <p className="text-xs text-muted-foreground">
                Used to identify the token in this list later. The token itself is opaque to the
                server.
              </p>
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
                        "focus-ring rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
                        active
                          ? "border-primary/60 bg-primary/15 text-link"
                          : "border-border bg-background text-muted-foreground hover:bg-muted"
                      )}
                      aria-pressed={active}
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
                  const active = selectedScopes.includes(scope);
                  return (
                    <button
                      key={scope}
                      type="button"
                      onClick={() => toggleScope(scope)}
                      className={cn(
                        "focus-ring rounded-full border px-2.5 py-1 font-mono text-[11px] font-medium transition-colors",
                        active
                          ? "border-primary/60 bg-primary/15 text-link"
                          : "border-border bg-background text-muted-foreground hover:bg-muted"
                      )}
                      aria-pressed={active}
                    >
                      {scope}
                    </button>
                  );
                })}
              </div>
              <p className="text-xs text-muted-foreground">
                Leave empty to inherit your existing scopes.
              </p>
            </div>

            <div className="flex items-center justify-end gap-2 pt-1">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setFormOpen(false);
                  resetForm();
                }}
                disabled={issue.isPending}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                variant="gradient"
                onClick={() => void handleGenerate()}
                disabled={!displayName.trim() || issue.isPending}
              >
                {issue.isPending ? "Generating…" : "Generate"}
              </Button>
            </div>
          </div>
        ) : null}

        {newlyIssued ? (
          <div className="rounded-md border border-[oklch(var(--co-amber-500)/0.4)] bg-[oklch(var(--co-amber-500)/0.08)] p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 space-y-1.5">
                <p className="text-sm font-semibold">New personal access token generated</p>
                <p className="text-xs text-muted-foreground">
                  For <span className="font-medium">{newlyIssued.display_name}</span>. This is the
                  only time the full token will be shown. Copy it now.
                </p>
                <code
                  className="block w-full break-all rounded bg-background/80 px-2 py-1.5 font-mono text-xs"
                  data-testid="pat-plaintext"
                >
                  {newlyIssued.plaintext_token}
                </code>
              </div>
              <div className="flex shrink-0 flex-col gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Copy token"
                  onClick={() => void handleCopy(newlyIssued.plaintext_token)}
                >
                  {copied ? (
                    <CheckCircle2 className="h-4 w-4 text-success" strokeWidth={1.6} />
                  ) : (
                    <Copy className="h-4 w-4" strokeWidth={1.6} />
                  )}
                </Button>
                <Button variant="ghost" size="sm" onClick={dismissNewlyIssued}>
                  Done
                </Button>
              </div>
            </div>
          </div>
        ) : null}

        {list.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-[68px] w-full rounded-md" />
            <Skeleton className="h-[68px] w-full rounded-md" />
          </div>
        ) : items.length > 0 ? (
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
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Revoke token ${pat.display_name}`}
                      disabled={revoke.isPending}
                      onClick={() => void revoke.mutateAsync({ pat_id: pat.id })}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" strokeWidth={1.6} />
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : !newlyIssued ? (
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
          {helpOpen ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
          How do I use this?
        </button>
        {helpOpen ? (
          <ol className="ml-6 list-decimal space-y-2 text-xs text-muted-foreground" data-testid="pat-help">
            <li>
              Copy the generated token (it looks like{" "}
              <code className="font-mono text-foreground">co_pat_abc123…</code>).
            </li>
            <li>
              In your AI client&apos;s MCP config, add a new server with URL{" "}
              <code className="font-mono text-foreground">
                {`${env.mcpBaseUrl}/mcp`}
              </code>
              .
            </li>
            <li>
              Set the auth header to{" "}
              <code className="font-mono text-foreground">REMOVED…</code>
              .
            </li>
            <li>
              Tools like{" "}
              <code className="font-mono text-foreground">list_people</code>,{" "}
              <code className="font-mono text-foreground">find_person_by_identifier</code>, etc.
              are now native to your AI.
            </li>
          </ol>
        ) : null}
      </CardContent>
    </Card>
  );
}
