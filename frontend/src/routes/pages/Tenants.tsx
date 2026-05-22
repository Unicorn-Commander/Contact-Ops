import { Plus, Save, Shield, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { useMcpMutation, useTenants } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import { cn } from "@/lib/utils";

const kindColors: Record<string, string> = {
  personal: "border-sky-200 bg-sky-50 text-sky-700",
  brand: "border-amber-200 bg-amber-50 text-amber-700",
  white_label_customer: "border-emerald-200 bg-emerald-50 text-emerald-700",
  magic_unicorn_internal: "border-violet-200 bg-violet-50 text-violet-700",
};

export function TenantsRoute() {
  const tenants = useTenants();
  const create = useMcpMutation(tools.createTenant);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Tenants</h1>
          <p className="text-sm text-muted-foreground">Manage tenant membership, branding, retention, and HIPAA flags.</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button className="bg-violet-500 hover:bg-violet-600">
              <Plus className="h-4 w-4" />
              Add tenant
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add tenant</DialogTitle>
              <DialogDescription>Visible only to authorized admins once role claims are enforced server-side.</DialogDescription>
            </DialogHeader>
            <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void create.mutateAsync({ display_name: name, slug: name.toLowerCase().replace(/[^a-z0-9]+/g, "-"), kind: "personal" }).then(() => setOpen(false)); }}>
              <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Tenant name" />
              <Button type="submit" className="bg-violet-500 hover:bg-violet-600">
                <Save className="mr-2 h-4 w-4" />
                Create tenant
              </Button>
            </form>
          </DialogContent>
        </Dialog>
      </div>

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
      ) : tenants.data?.items?.length ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {tenants.data.items.map((tenant) => (
            <Card key={tenant.tenant_id} className="border-violet-200 hover:border-violet-300 transition-colors">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  {tenant.display_name}
                  {tenant.hipaa_mode && (
                    <ShieldCheck className="h-4 w-4 text-amber-500" aria-label="HIPAA enabled" />
                  )}
                </CardTitle>
                <CardDescription className="font-mono text-xs">{tenant.slug}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                <Badge className={cn(kindColors[tenant.kind ?? ""] ?? "border-slate-200 bg-slate-50 text-slate-700", "border")}>
                  {tenant.kind?.replace(/_/g, " ") ?? "tenant"}
                </Badge>
                <Badge variant="secondary">{tenant.role ?? "member"}</Badge>
                {tenant.retention_class && (
                  <Badge variant="outline">{tenant.retention_class}</Badge>
                )}
                {tenant.hipaa_mode && (
                  <Badge variant="destructive" className="flex items-center gap-1">
                    <Shield className="h-3 w-3" />
                    HIPAA
                  </Badge>
                )}
                <Badge variant="secondary" className="ml-auto">
                  {tenant.member_count ?? 0} members
                </Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div className="text-center py-12">
          <Shield className="mx-auto h-12 w-12 text-violet-400 mb-4" />
          <h2 className="mb-2 text-xl font-semibold text-violet-600">No tenants</h2>
          <p className="text-muted-foreground max-w-xl mx-auto">
            Tenants allow you to segment your contact data. Create your first tenant to get started.
          </p>
          <Button 
            className="mt-6 bg-violet-500 hover:bg-violet-600"
            onClick={() => setOpen(true)}
          >
            <Plus className="mr-2 h-4 w-4" />
            Create tenant
          </Button>
        </div>
      )}
    </div>
  );
}
