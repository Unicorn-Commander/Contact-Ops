/**
 * Workspace members — Settings card (Team surface).
 *
 * Mirrors the PAT / CardDAV cards' shape:
 *   - Header with title + description + "Invite" button (top-right)
 *   - Expanded inline invite form (email + role chips) — NOT a modal
 *   - Member list: name/email, role (editable <select>), status, remove
 *   - Admin-only: a non-admin caller's list query fails INSUFFICIENT_ROLE, so we
 *     render an admin-only notice instead of the table
 *   - Empty state when the workspace has only you
 *
 * Backend tools (mcp/tools/tenancy.py): list_workspace_members,
 * invite_workspace_member, update_workspace_member_role, revoke_workspace_member.
 * Authority (workspace admin) + anti-lockout (last-admin, self) are enforced
 * server-side; this UI surfaces the resulting errors via toasts.
 */
import { useState } from "react";
import { Mail, ShieldCheck, Trash2, UserPlus, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
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
  useInviteMemberMutation,
  useRevokeMemberMutation,
  useUpdateMemberRoleMutation,
  useWorkspaceMembers
} from "@/hooks/useWorkspaceMembers";
import { MCPError } from "@/lib/mcp";
import type { WorkspaceMember, WorkspaceRole } from "@/lib/types";
import { cn, compactDate, initials } from "@/lib/utils";

const ROLES: WorkspaceRole[] = ["viewer", "member", "manager", "admin"];

const ROLE_HELP: Record<WorkspaceRole, string> = {
  viewer: "Read-only access",
  member: "Standard access",
  manager: "Manage contacts & data",
  admin: "Full access incl. members & settings"
};

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function memberLabel(m: WorkspaceMember): { primary: string; secondary: string } {
  const name = m.full_name?.trim();
  const email = m.email?.trim();
  if (name && email) return { primary: name, secondary: email };
  if (email) return { primary: email, secondary: m.username ?? "" };
  if (name) return { primary: name, secondary: m.username ?? "" };
  if (m.username) return { primary: m.username, secondary: m.user_uc_uid };
  // Directory unconfigured / user unreadable — show the raw subject.
  return { primary: m.user_uc_uid, secondary: "" };
}

export function WorkspaceMembersSection() {
  const list = useWorkspaceMembers();
  const invite = useInviteMemberMutation();
  const updateRole = useUpdateMemberRoleMutation();
  const revoke = useRevokeMemberMutation();

  const [formOpen, setFormOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("member");

  // A non-admin caller is rejected by the backend. Render an admin-only notice
  // rather than an error spew (the members surface is admin-managed by design).
  const notAdmin =
    list.error instanceof MCPError && list.error.code === "INSUFFICIENT_ROLE";

  const members: WorkspaceMember[] = list.data?.items ?? [];
  const emailValid = EMAIL_RE.test(email.trim());

  const handleInvite = async () => {
    if (!emailValid) return;
    try {
      await invite.mutateAsync({ email: email.trim(), role });
      setEmail("");
      setRole("member");
      setFormOpen(false);
    } catch {
      // toast handled in the hook (keeps the form open so they can retry/fix)
    }
  };

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2 text-base">
            <Users className="h-4 w-4 text-[oklch(var(--co-fuchsia-500))]" strokeWidth={1.8} />
            Members
          </CardTitle>
          <CardDescription>
            People who can access this workspace. Invite teammates by email and manage their roles.
          </CardDescription>
        </div>
        {!notAdmin ? (
          <Button
            size="sm"
            disabled={formOpen || invite.isPending}
            onClick={() => setFormOpen(true)}
          >
            <UserPlus className="h-4 w-4" strokeWidth={1.6} />
            Invite
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {notAdmin ? (
          <div className="flex flex-col items-center gap-2 py-8 text-center text-muted-foreground">
            <ShieldCheck className="h-8 w-8" strokeWidth={1.5} />
            <p className="text-sm">Only workspace admins can manage members.</p>
            <p className="text-xs">Ask an admin of this workspace to invite or remove people.</p>
          </div>
        ) : (
          <>
            {formOpen ? (
              <div className="space-y-3 rounded-md border border-border bg-muted/30 p-3">
                <div className="space-y-1.5">
                  <label htmlFor="invite-email" className="block text-sm font-medium text-foreground">
                    Email address
                  </label>
                  <Input
                    id="invite-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="REMOVED"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && emailValid) void handleInvite();
                    }}
                  />
                  <p className="text-xs text-muted-foreground">
                    They must already have a Unicorn Commander account (have signed in at least once).
                  </p>
                </div>

                <div className="space-y-1.5">
                  <label className="block text-sm font-medium text-foreground">Role</label>
                  <div className="flex flex-wrap gap-1.5">
                    {ROLES.map((r) => {
                      const active = role === r;
                      return (
                        <button
                          key={r}
                          type="button"
                          onClick={() => setRole(r)}
                          className={cn(
                            "focus-ring rounded-full border px-2.5 py-1 text-xs font-medium capitalize transition-colors",
                            active
                              ? "border-primary/60 bg-primary/15 text-link"
                              : "border-border bg-background text-muted-foreground hover:bg-muted"
                          )}
                          aria-pressed={active}
                        >
                          {r}
                        </button>
                      );
                    })}
                  </div>
                  <p className="text-xs text-muted-foreground">{ROLE_HELP[role]}</p>
                </div>

                <div className="flex items-center justify-end gap-2 pt-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setFormOpen(false);
                      setEmail("");
                      setRole("member");
                    }}
                    disabled={invite.isPending}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    variant="gradient"
                    onClick={() => void handleInvite()}
                    disabled={!emailValid || invite.isPending}
                  >
                    {invite.isPending ? "Inviting…" : "Send invite"}
                  </Button>
                </div>
              </div>
            ) : null}

            {list.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-[60px] w-full rounded-md" />
                <Skeleton className="h-[60px] w-full rounded-md" />
              </div>
            ) : members.length > 0 ? (
              <ul className="space-y-2" aria-label="Workspace members">
                {members.map((m) => {
                  const { primary, secondary } = memberLabel(m);
                  const busy = updateRole.isPending || revoke.isPending;
                  return (
                    <li
                      key={m.user_uc_uid}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3 transition-colors hover:bg-muted/50"
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/12 text-xs font-semibold text-link">
                          {initials(primary)}
                        </div>
                        <div className="min-w-0">
                          <p className="flex items-center gap-1.5 truncate font-medium text-foreground">
                            {primary}
                            {m.is_self ? (
                              <Badge variant="outline" className="rounded text-[10px]">
                                You
                              </Badge>
                            ) : null}
                          </p>
                          <p className="truncate text-xs text-muted-foreground">
                            {secondary}
                            {m.added_at ? (
                              <span className="co-mono-numeric">
                                {secondary ? " · " : ""}Joined {compactDate(m.added_at)}
                              </span>
                            ) : null}
                          </p>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <select
                          aria-label={`Role for ${primary}`}
                          value={m.role}
                          disabled={busy}
                          onChange={(e) =>
                            void updateRole.mutateAsync({
                              user_uc_uid: m.user_uc_uid,
                              role: e.target.value as WorkspaceRole
                            })
                          }
                          className="focus-ring h-8 rounded-md border border-border bg-background px-2 text-xs font-medium capitalize text-foreground"
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r} className="capitalize">
                              {r}
                            </option>
                          ))}
                        </select>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Remove ${primary}`}
                          disabled={busy || m.is_self}
                          title={m.is_self ? "You can't remove yourself" : "Remove from workspace"}
                          onClick={() =>
                            void revoke.mutateAsync({ user_uc_uid: m.user_uc_uid })
                          }
                        >
                          <Trash2 className="h-4 w-4 text-destructive" strokeWidth={1.6} />
                        </Button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="flex flex-col items-center gap-2 py-8 text-center text-muted-foreground">
                <Mail className="h-8 w-8" strokeWidth={1.5} />
                <p className="text-sm">No teammates yet.</p>
                <p className="text-xs">Invite someone by email to share this workspace.</p>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
