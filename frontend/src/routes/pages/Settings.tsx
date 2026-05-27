import { Copy, KeyRound, LogOut, Moon, Plus, Sun, Trash2 } from "lucide-react";
import { useAuth } from "react-oidc-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
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
  const passwords = useCardDavPasswords();
  const generate = useMcpMutation(tools.generateCardDavPassword);
  const revoke = useMcpMutation(tools.revokeCardDavPassword);

  const profile = auth.user?.profile as Record<string, unknown> | undefined;
  const name = String(profile?.name ?? "");
  const email = String(profile?.email ?? "");
  const pronouns = String(profile?.pronouns ?? "");
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
        <p className="mt-1 text-sm text-muted-foreground">Profile, CardDAV app passwords, appearance, and session controls.</p>
      </div>

      {/* Profile */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Profile</CardTitle>
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
            <ReadonlyField id="profile_pronouns" label="Pronouns" value={pronouns || "Not set"} />
            <ReadonlyField id="profile_timezone" label="Time zone" value={Intl.DateTimeFormat().resolvedOptions().timeZone} />
          </div>
        </CardContent>
      </Card>

      {/* Appearance */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Appearance</CardTitle>
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
              <KeyRound className="h-4 w-4 text-muted-foreground" strokeWidth={1.6} />
              CardDAV app passwords
            </CardTitle>
            <CardDescription>Generated passwords are shown once by the MCP tool response.</CardDescription>
          </div>
          <Button
            size="sm"
            disabled={generate.isPending}
            onClick={() => void generate.mutateAsync({ label: `Browser ${new Date().toISOString()}` })}
          >
            <Plus className="h-4 w-4" strokeWidth={1.6} />
            Generate
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
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
                  <Button variant="ghost" size="icon" aria-label="Copy password">
                    <Copy className="h-4 w-4" strokeWidth={1.6} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Revoke password"
                    disabled={revoke.isPending}
                    onClick={() => void revoke.mutateAsync({ password_id: password.id })}
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
