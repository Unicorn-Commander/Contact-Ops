import { Plus, Save } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useMcpMutation, useTenants } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";

export function TenantsRoute() {
  const tenants = useTenants();
  const create = useMcpMutation(tools.createTenant);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Tenants</h1>
          <p className="text-sm text-muted-foreground">Manage tenant membership, branding, retention, and HIPAA flags.</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild><Button><Plus className="h-4 w-4" /> Add tenant</Button></DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>Add tenant</DialogTitle><DialogDescription>Visible only to authorized admins once role claims are enforced server-side.</DialogDescription></DialogHeader>
            <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void create.mutateAsync({ display_name: name, slug: name.toLowerCase().replace(/[^a-z0-9]+/g, "-"), kind: "personal" }).then(() => setOpen(false)); }}>
              <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Tenant name" />
              <Button type="submit"><Save className="h-4 w-4" /> Create tenant</Button>
            </form>
          </DialogContent>
        </Dialog>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {tenants.data?.items?.map((tenant) => (
          <Card key={tenant.tenant_id}>
            <CardHeader>
              <CardTitle>{tenant.display_name}</CardTitle>
              <CardDescription>{tenant.slug}</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              <Badge>{tenant.kind ?? "tenant"}</Badge>
              <Badge>{tenant.role ?? "member"}</Badge>
              <Badge>{tenant.retention_class ?? "retention default"}</Badge>
              {tenant.hipaa_mode ? <Badge className="text-amber-700">HIPAA</Badge> : null}
              <Badge>{tenant.member_count ?? 0} members</Badge>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
