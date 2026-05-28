import { Building2, Plus, Save, Shield, ShieldCheck, Users } from "lucide-react";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { StatStrip, StatTile, StatTileSkeleton } from "@/components/domain/Stats";
import { useMcpMutation, useTenants } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import { cn } from "@/lib/utils";

// Tenant kind → tokenized accent. The label uses `text-foreground` (always AA);
// the kind's semantic color is carried by the chip's tint, border, and dot — so a
// faint sky/amber tint can't drop the *text* below contrast. No palette hex.
const kindAccent: Record<string, { ring: string; dot: string }> = {
  personal: { ring: "border-info/40 bg-info/10", dot: "bg-info" },
  brand: { ring: "border-warning/40 bg-warning/10", dot: "bg-warning" },
  white_label_customer: { ring: "border-success/40 bg-success/10", dot: "bg-success" },
  magic_unicorn_internal: { ring: "border-primary/40 bg-primary/10", dot: "bg-primary" }
};

function KindChip({ kind }: { kind?: string }) {
  const accent = kindAccent[kind ?? ""];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium capitalize text-foreground",
        accent?.ring ?? "border-border bg-muted"
      )}
    >
      <span aria-hidden className={cn("h-1.5 w-1.5 shrink-0 rounded-full", accent?.dot ?? "bg-muted-foreground")} />
      {kind?.replace(/_/g, " ") ?? "tenant"}
    </span>
  );
}

function AddTenantDialog({
  open,
  onOpenChange,
  hideTrigger = false
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  hideTrigger?: boolean;
}) {
  const create = useMcpMutation(tools.createTenant);
  const [name, setName] = useState("");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {hideTrigger ? null : (
        <DialogTrigger asChild>
          <Button>
            <Plus className="h-4 w-4" strokeWidth={1.6} />
            Add tenant
          </Button>
        </DialogTrigger>
      )}
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add tenant</DialogTitle>
          <DialogDescription>Visible only to authorized admins once role claims are enforced server-side.</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void create
              .mutateAsync({ display_name: name, slug: name.toLowerCase().replace(/[^a-z0-9]+/g, "-"), kind: "personal" })
              .then(() => onOpenChange(false));
          }}
        >
          <div className="space-y-1.5">
            <label htmlFor="tenant_name" className="text-sm font-medium text-foreground">
              Tenant name
            </label>
            <Input id="tenant_name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Magic Unicorn" />
          </div>
          <div className="flex justify-end pt-1">
            <Button type="submit" disabled={create.isPending || !name.trim()}>
              <Save className="h-4 w-4" strokeWidth={1.6} />
              {create.isPending ? "Creating…" : "Create tenant"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function TenantsRoute() {
  const tenants = useTenants();
  const [open, setOpen] = useState(false);
  const items = useMemo(() => tenants.data?.items ?? [], [tenants.data]);

  const totalMembers = useMemo(() => items.reduce((sum, t) => sum + (t.member_count ?? 0), 0), [items]);
  const hipaaCount = useMemo(() => items.filter((t) => t.hipaa_mode).length, [items]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="inline-flex h-6 items-center rounded-full border border-primary/30 bg-primary/10 px-2 text-[11px] font-semibold uppercase tracking-wider text-primary">
              Admin
            </span>
            <h1 className="text-2xl font-semibold tracking-tight">Tenants</h1>
            {!tenants.isLoading && tenants.data ? (
              <span className="co-mono-numeric rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                {items.length}
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">Manage tenant membership, branding, retention, and HIPAA flags.</p>
        </div>
        <Button variant="gradient" className="h-9" onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" strokeWidth={1.8} />
          Add tenant
        </Button>
        <AddTenantDialog open={open} onOpenChange={setOpen} hideTrigger />
      </div>

      {/* Overview stat strip */}
      {tenants.isLoading ? (
        <StatStrip>
          <StatTileSkeleton />
          <StatTileSkeleton />
          <StatTileSkeleton />
        </StatStrip>
      ) : items.length ? (
        <StatStrip>
          <StatTile label="Tenants" value={items.length} icon={Building2} tone="sky" />
          <StatTile label="Members" value={totalMembers} icon={Users} tone="emerald" hint="across all workspaces" />
          <StatTile
            label="HIPAA tenants"
            value={hipaaCount}
            icon={ShieldCheck}
            tone="amber"
            hint={hipaaCount === 1 ? "compliance-gated" : "compliance-gated"}
          />
        </StatStrip>
      ) : null}

      {tenants.isLoading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardHeader>
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-4 w-20" />
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                <Skeleton className="h-5 w-16" />
                <Skeleton className="h-5 w-20" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : items.length ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {items.map((tenant) => (
            <Card key={tenant.tenant_id} className="transition-colors hover:border-primary/30">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  {tenant.display_name}
                  {tenant.hipaa_mode ? (
                    <ShieldCheck className="h-4 w-4 text-warning" strokeWidth={1.8} aria-label="HIPAA enabled" />
                  ) : null}
                </CardTitle>
                <CardDescription className="co-mono-numeric text-xs">{tenant.slug}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center gap-2">
                <KindChip kind={tenant.kind} />
                <Badge variant="secondary" className="capitalize">
                  {tenant.role ?? "member"}
                </Badge>
                {tenant.retention_class ? <Badge variant="outline">{tenant.retention_class}</Badge> : null}
                {tenant.hipaa_mode ? (
                  // Danger is carried by the red shield (graphical, 3:1) + border; the
                  // label stays text-foreground so it clears AA on the tinted bg.
                  <span className="inline-flex items-center gap-1 rounded-full border border-destructive/50 bg-destructive/10 px-2 py-0.5 text-xs font-semibold text-foreground">
                    <Shield className="h-3 w-3 text-destructive" strokeWidth={1.8} />
                    HIPAA
                  </span>
                ) : null}
                <span className="co-mono-numeric ml-auto text-xs text-muted-foreground">
                  {tenant.member_count ?? 0} members
                </span>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center gap-4 py-16 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Building2 className="h-6 w-6" strokeWidth={1.5} />
            </div>
            <div className="space-y-1">
              <h2 className="text-lg font-semibold">No tenants yet</h2>
              <p className="mx-auto max-w-md text-sm text-muted-foreground">
                Tenants segment your contact data into isolated workspaces. Create your first to get started.
              </p>
            </div>
            <Button onClick={() => setOpen(true)}>
              <Plus className="h-4 w-4" strokeWidth={1.6} />
              Create tenant
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
