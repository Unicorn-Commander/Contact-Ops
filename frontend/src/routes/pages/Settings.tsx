import { Copy, KeyRound, LogOut, Moon, Palette, Plus, Sun, Trash2, UserCircle } from "lucide-react";
import { useAuth } from "react-oidc-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { NotesSection } from "@/components/profile/NotesSection";
import { PersonalAccessTokensSection } from "@/components/profile/PersonalAccessTokensSection";
import { WorkspaceMembersSection } from "@/components/profile/WorkspaceMembersSection";
import { useConsent } from "@/lib/consent";
import { useCardDavPasswords, useMcpMutation } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import { compactDate, initials } from "@/lib/utils";
import {
  applyThemePreference,
  getThemePreference,
  setThemePreference,
  type ThemeMode
} from "@/design-system/tokens";
import { useEffect, useState } from "react";

function ReadonlyField({ id, label, value, mono = false }: { id: string; label: string; value: string; mono?: boolean }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-muted-foreground">
        {label}
      </label>
      <Input id={id} value={value} readOnly className={mono ? "co-mono-numeric bg-muted" : "bg-muted"} />
    </div>
  );
}

export function SettingsRoute() {
  const auth = useAuth();
  const [theme, setTheme] = useState<ThemeMode>(() => getThemePreference());
  const { consent, setConsent } = useConsent();
  const passwords = useCardDavPasswords();
  const generate = useMcpMutation(tools.generateCardDavPassword);
  const revoke = useMcpMutation(tools.revokeCardDavPassword);

  // Server emits the plaintext password only once; capture it for the
  // post-generate callout below so the user can copy it before it's gone.
  const [newCredential, setNewCredential] = useState<{
    device_label: string;
    app_password_plaintext: string;
    last_4_chars: string;
  } | null>(null);

  const handleGenerateClick = async () => {
    try {
      const result = (await generate.mutateAsync({
        device_label: `Browser ${new Date().toLocaleString()}`
      })) as { device_label: string; app_password_plaintext: string; last_4_chars: string };
      setNewCredential(result);
    } catch (err) {
      console.error("CardDAV generate failed", err);
    }
  };

  const profile = auth.user?.profile as Record<string, unknown> | undefined;
  const name = String(profile?.name ?? "");
  const email = String(profile?.email ?? "");
  const preferredUsername = String(profile?.preferred_username ?? "");

  // Apply the chosen theme through the design-system helper so it takes effect
  // live and stays consistent with the AppShell toggle (both persist + apply).
  useEffect(() => {
    applyThemePreference(theme);
  }, [theme]);

  const dark = theme === "dark";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">Profile, CardDAV app passwords, personal access tokens, appearance, and session controls.</p>
      </div>

      {/* Profile */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <UserCircle className="h-4 w-4 text-[oklch(var(--co-sky-500))]" strokeWidth={1.8} />
            Profile
          </CardTitle>
          <CardDescription>Values come from your Keycloak profile claims.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex items-center gap-4">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-primary/12 text-xl font-semibold text-link">
              {name ? initials(name) : "?"}
            </div>
            <div className="min-w-0">
              <p className="truncate text-lg font-medium">{name || "No name set"}</p>
              <p className="truncate text-sm text-muted-foreground">{email}</p>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <ReadonlyField id="profile_name" label="Name" value={name} />
            <ReadonlyField id="profile_email" label="Email" value={email} />
            <ReadonlyField id="profile_username" label="Username" value={preferredUsername} mono />
            <ReadonlyField id="profile_timezone" label="Time zone" value={Intl.DateTimeFormat().resolvedOptions().timeZone} />
          </div>
        </CardContent>
      </Card>

      {/* Members — workspace team management (admins only; self-gates otherwise). */}
      <WorkspaceMembersSection />

      {/* Privacy & Analytics — opt-in consent, revocable. Analytics never fire
          until accepted, and only when the trackers are configured server-side. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Palette className="h-4 w-4 text-[oklch(var(--co-emerald-500))]" strokeWidth={1.8} />
            Privacy &amp; Analytics
          </CardTitle>
          <CardDescription>
            Privacy-friendly analytics, opt-in. We never capture your contact data.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {([
            ["web", "Website analytics", "Anonymous page-visit stats (Umami)."],
            ["product", "Product analytics", "Which features are used, by your opaque account id (PostHog) — never your name or email."]
          ] as const).map(([key, label, desc]) => {
            const on = consent[key];
            return (
              <div key={key} className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-sm font-medium">{label}</p>
                  <p className="text-xs text-muted-foreground">{desc}</p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="w-20 shrink-0"
                  aria-pressed={on}
                  onClick={() => setConsent({ [key]: !on })}
                >
                  {on ? "On" : "Off"}
                </Button>
              </div>
            );
          })}
        </CardContent>
      </Card>

      {/* Notes & Log — Phase 1 user-target surface; same component drops into person/org pages later. */}
      <NotesSection targetType="user" />

      {/* Appearance */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Palette className="h-4 w-4 text-[oklch(var(--co-fuchsia-500))]" strokeWidth={1.8} />
            Appearance
          </CardTitle>
          <CardDescription>Override the OS color scheme for this browser.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {dark ? (
                <Moon className="h-5 w-5 text-muted-foreground" strokeWidth={1.6} />
              ) : (
                <Sun className="h-5 w-5 text-muted-foreground" strokeWidth={1.6} />
              )}
              <span className="text-sm font-medium">{dark ? "Dark mode" : "Light mode"}</span>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="w-24"
              aria-label={`Switch to ${dark ? "light" : "dark"} mode`}
              onClick={() => {
                const next: ThemeMode = dark ? "light" : "dark";
                setThemePreference(next);
                setTheme(next);
              }}
            >
              {dark ? "Light" : "Dark"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* CardDAV passwords */}
      <Card>
        <CardHeader className="flex-row items-start justify-between space-y-0">
          <div className="space-y-1.5">
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRound className="h-4 w-4 text-[oklch(var(--co-amber-500))]" strokeWidth={1.8} />
              CardDAV app passwords
            </CardTitle>
            <CardDescription>Generated passwords are shown once by the MCP tool response.</CardDescription>
          </div>
          <Button
            size="sm"
            disabled={generate.isPending}
            onClick={() => void handleGenerateClick()}
          >
            <Plus className="h-4 w-4" strokeWidth={1.6} />
            Generate
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {newCredential ? (
            <div className="rounded-md border border-[oklch(var(--co-amber-500)/0.4)] bg-[oklch(var(--co-amber-500)/0.08)] p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 space-y-1.5">
                  <p className="text-sm font-semibold">New app password generated</p>
                  <p className="text-xs text-muted-foreground">
                    For <span className="font-medium">{newCredential.device_label}</span>. This is the only time
                    the full password will be shown — copy it now.
                  </p>
                  <code className="block w-full break-all rounded bg-background/80 px-2 py-1 font-mono text-xs">
                    {newCredential.app_password_plaintext}
                  </code>
                </div>
                <div className="flex shrink-0 flex-col gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Copy app password"
                    onClick={() => void navigator.clipboard.writeText(newCredential.app_password_plaintext)}
                  >
                    <Copy className="h-4 w-4" strokeWidth={1.6} />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setNewCredential(null)}>
                    Done
                  </Button>
                </div>
              </div>
            </div>
          ) : null}
          {passwords.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-[60px] w-full rounded-md" />
              <Skeleton className="h-[60px] w-full rounded-md" />
            </div>
          ) : passwords.data?.items?.length ? (
            passwords.data.items.map((password) => (
              <div
                key={password.id}
                className="flex items-center justify-between rounded-md border border-border p-3 transition-colors hover:bg-muted/50"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium">{password.label}</p>
                  <p className="co-mono-numeric text-sm text-muted-foreground">
                    Created {compactDate(password.created_at)}
                    {password.last_used_at ? ` · Last used ${compactDate(password.last_used_at)}` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 gap-1">
                  {/* No copy button here: the plaintext is shown exactly once at
                      creation (the callout above). A saved row holds only the
                      last-4, so there is nothing to copy — a button that can't
                      work is worse than none. */}
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Revoke password"
                    disabled={revoke.isPending}
                    onClick={() => void revoke.mutateAsync({ app_password_id: password.id })}
                  >
                    <Trash2 className="h-4 w-4 text-destructive" strokeWidth={1.6} />
                  </Button>
                </div>
              </div>
            ))
          ) : (
            <div className="flex flex-col items-center gap-2 py-8 text-center text-muted-foreground">
              <KeyRound className="h-8 w-8" strokeWidth={1.5} />
              <p className="text-sm">No CardDAV app passwords have been created.</p>
              <p className="text-xs">Generate one above to connect your contacts with CardDAV clients.</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Personal Access Tokens — paired with CardDAV; both are scoped credentials. */}
      <PersonalAccessTokensSection />

      {/* Session */}
      <Card className="border-destructive/30">
        <CardHeader>
          <CardTitle className="text-base text-destructive">Session</CardTitle>
          <CardDescription>End your current session.</CardDescription>
        </CardHeader>
        <CardContent>
          {/* Destructive-outline (not solid bg-destructive): text-destructive meets AA, while
              the solid destructive token's white-on-rose-500 is 4.07:1 — see report. */}
          <Button
            variant="outline"
            className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={() => void auth.signoutRedirect()}
            aria-label="Sign out of Contact-Ops"
          >
            <LogOut className="h-4 w-4" strokeWidth={1.6} />
            Sign out
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
