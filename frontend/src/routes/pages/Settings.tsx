import { Copy, KeyRound, LogOut, Moon, Plus, Sun, Trash2 } from "lucide-react";
import { useAuth } from "react-oidc-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useCardDavPasswords, useMcpMutation } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import { compactDate } from "@/lib/utils";
import { useEffect, useState } from "react";

export function SettingsRoute() {
  const auth = useAuth();
  const [dark, setDark] = useState(() => localStorage.getItem("theme") !== "light");
  const passwords = useCardDavPasswords();
  const generate = useMcpMutation(tools.generateCardDavPassword);
  const revoke = useMcpMutation(tools.revokeCardDavPassword);

  const profile = auth.user?.profile as Record<string, unknown> | undefined;
  const name = String(profile?.name ?? "");
  const email = String(profile?.email ?? "");
  const pronouns = String(profile?.pronouns ?? "");
  const preferredUsername = String(profile?.preferred_username ?? "");

  useEffect(() => {
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">Profile, CardDAV app passwords, notifications, and session controls.</p>
      </div>
      
      {/* Profile Section */}
      <Card className="border-violet-200">
        <CardHeader>
          <CardTitle className="text-violet-600">Profile</CardTitle>
          <CardDescription>Values come from Keycloak profile claims.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-4 mb-6">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-violet-500/20 text-violet-500 text-xl font-semibold">
              {name ? name.charAt(0).toUpperCase() : "?"}
            </div>
            <div>
              <p className="text-lg font-medium">{name || "No name set"}</p>
              <p className="text-sm text-muted-foreground">{email}</p>
            </div>
          </div>
          <div className="space-y-3">
            <div>
              <label className="text-sm font-medium text-muted-foreground mb-1 block">Name</label>
              <Input value={name} readOnly className="bg-muted" />
            </div>
            <div>
              <label className="text-sm font-medium text-muted-foreground mb-1 block">Email</label>
              <Input value={email} readOnly className="bg-muted" />
            </div>
            <div>
              <label className="text-sm font-medium text-muted-foreground mb-1 block">Username</label>
              <Input value={preferredUsername} readOnly className="bg-muted font-mono" />
            </div>
            <div>
              <label className="text-sm font-medium text-muted-foreground mb-1 block">Pronouns</label>
              <Input value={pronouns || "Not set"} readOnly className="bg-muted" />
            </div>
            <div>
              <label className="text-sm font-medium text-muted-foreground mb-1 block">Time zone</label>
              <Input value={Intl.DateTimeFormat().resolvedOptions().timeZone} readOnly className="bg-muted" />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Theme */}
      <Card className="border-violet-200">
        <CardHeader>
          <CardTitle className="text-violet-600">Appearance</CardTitle>
          <CardDescription>Toggle between light and dark mode.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {dark ? <Moon className="h-5 w-5 text-violet-500" /> : <Sun className="h-5 w-5 text-violet-500" />}
              <span className="text-sm font-medium">{dark ? "Dark mode" : "Light mode"}</span>
            </div>
            <Button 
              variant="outline" 
              size="sm"
              onClick={() => setDark(!dark)}
              className="w-24"
            >
              {dark ? "Light" : "Dark"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* CardDAV Passwords */}
      <Card className="border-violet-200">
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-violet-600">
              <KeyRound className="h-4 w-4" />
              CardDAV app passwords
            </CardTitle>
            <CardDescription>Generated passwords are shown once by the MCP tool response.</CardDescription>
          </div>
          <Button 
            variant="default" 
            size="sm"
            className="bg-violet-500 hover:bg-violet-600"
            onClick={() => void generate.mutateAsync({ label: `Browser ${new Date().toISOString()}` })}
          >
            <Plus className="mr-1 h-4 w-4" />
            Generate
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {passwords.isLoading ? (
            <div className="space-y-2">
              <div className="h-16 animate-pulse rounded-md bg-muted" />
              <div className="h-16 animate-pulse rounded-md bg-muted" />
            </div>
          ) : passwords.data?.items?.length ? (
            passwords.data.items.map((password) => (
              <div key={password.id} className="flex items-center justify-between rounded-md border p-3 hover:bg-muted/50 transition-colors">
                <div>
                  <p className="font-medium">{password.label}</p>
                  <p className="text-sm text-muted-foreground font-mono">
                    Created {compactDate(password.created_at)}
                    {password.last_used_at ? ` · Last used ${compactDate(password.last_used_at)}` : ""}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button variant="ghost" size="icon" aria-label="Copy password">
                    <Copy className="h-4 w-4" />
                  </Button>
                  <Button 
                    variant="ghost" 
                    size="icon" 
                    aria-label="Revoke password"
                    onClick={() => void revoke.mutateAsync({ password_id: password.id })}
                  >
                    <Trash2 className="h-4 w-4 text-rose-500" />
                  </Button>
                </div>
              </div>
            ))
          ) : (
            <div className="text-center py-8">
              <KeyRound className="mx-auto h-8 w-8 text-muted-foreground mb-2" />
              <p className="text-sm text-muted-foreground">No CardDAV app passwords have been created.</p>
              <p className="text-xs text-muted-foreground mt-1">Generate one above to connect your contacts with CardDAV clients.</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Sign Out */}
      <Card className="border-rose-200">
        <CardHeader>
          <CardTitle className="text-rose-600">Session</CardTitle>
          <CardDescription>End your current session.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button 
            variant="destructive" 
            onClick={() => void auth.signoutRedirect()}
            aria-label="Sign out of Contact-Ops"
          >
            <LogOut className="mr-2 h-4 w-4" />
            Sign out
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
