import { Copy, KeyRound, LogOut, Plus, Trash2 } from "lucide-react";
import { useAuth } from "react-oidc-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useCardDavPasswords, useMcpMutation } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import { compactDate } from "@/lib/utils";

export function SettingsRoute() {
  const auth = useAuth();
  const passwords = useCardDavPasswords();
  const generate = useMcpMutation(tools.generateCardDavPassword);
  const revoke = useMcpMutation(tools.revokeCardDavPassword);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">Profile, CardDAV app passwords, notifications, and session controls.</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Profile</CardTitle><CardDescription>Values come from Keycloak profile claims.</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            <Input value={auth.user?.profile.name ?? ""} readOnly />
            <Input value={auth.user?.profile.email ?? ""} readOnly />
            <Input placeholder="Time zone" defaultValue={Intl.DateTimeFormat().resolvedOptions().timeZone} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Notifications</CardTitle><CardDescription>Phase 3 will attach agent and approval notification controls.</CardDescription></CardHeader>
          <CardContent className="text-sm text-muted-foreground">No notification channels configured yet.</CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2"><KeyRound className="h-4 w-4" /> CardDAV app passwords</CardTitle>
            <CardDescription>Generated passwords are shown once by the MCP tool response.</CardDescription>
          </div>
          <Button variant="outline" onClick={() => void generate.mutateAsync({ label: `Browser ${new Date().toISOString()}` })}><Plus className="h-4 w-4" /> Generate</Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {passwords.data?.items?.length ? passwords.data.items.map((password) => (
            <div key={password.id} className="flex items-center justify-between rounded-md border p-3">
              <div>
                <p className="font-medium">{password.label}</p>
                <p className="text-sm text-muted-foreground">Created {compactDate(password.created_at)} · Last used {compactDate(password.last_used_at)}</p>
              </div>
              <div className="flex gap-2">
                <Button variant="ghost" size="icon"><Copy className="h-4 w-4" /></Button>
                <Button variant="ghost" size="icon" onClick={() => void revoke.mutateAsync({ password_id: password.id })}><Trash2 className="h-4 w-4" /></Button>
              </div>
            </div>
          )) : <p className="text-sm text-muted-foreground">No CardDAV app passwords have been created.</p>}
        </CardContent>
      </Card>
      <Button variant="destructive" onClick={() => void auth.signoutRedirect()}><LogOut className="h-4 w-4" /> Sign out</Button>
    </div>
  );
}
