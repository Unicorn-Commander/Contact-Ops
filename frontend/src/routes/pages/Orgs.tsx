import { zodResolver } from "@hookform/resolvers/zod";
import { useParams } from "@tanstack/react-router";
import { Plus, Save, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { OrgCard } from "@/components/domain/Cards";
import { ContactMethodList } from "@/components/domain/Lists";
import { useMcpMutation, useOrganization, useOrganizations } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import { AuditList } from "@/routes";

const orgSchema = z.object({
  legal_name: z.string().min(1),
  display_name: z.string().optional(),
  domain: z.string().optional(),
  industry: z.string().optional()
});

function OrgForm({ onSaved }: { onSaved: () => void }) {
  const form = useForm<z.infer<typeof orgSchema>>({ resolver: zodResolver(orgSchema), defaultValues: { legal_name: "" } });
  const create = useMcpMutation(tools.createOrganization);
  return (
    <form
      className="space-y-3"
      onSubmit={form.handleSubmit(async (values) => {
        await create.mutateAsync({
          legal_name: values.legal_name,
          display_name: values.display_name || undefined,
          domain: values.domain || undefined,
          industry: values.industry || undefined
        });
        onSaved();
      })}
    >
      <Input placeholder="Legal name" {...form.register("legal_name")} />
      <Input placeholder="Display name" {...form.register("display_name")} />
      <Input placeholder="Domain" {...form.register("domain")} />
      <Input placeholder="Industry" {...form.register("industry")} />
      <Button type="submit" disabled={create.isPending}>
        <Save className="h-4 w-4" />
        Create organization
      </Button>
    </form>
  );
}

export function OrgsRoute() {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const args = useMemo(() => ({ query: query || undefined, limit: 50 }), [query]);
  const orgs = useOrganizations(args);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Organizations</h1>
          <p className="text-sm text-muted-foreground">Browse canonical companies, nonprofits, agencies, and groups.</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button><Plus className="h-4 w-4" /> Add organization</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add organization</DialogTitle>
              <DialogDescription>Create through create_organization.</DialogDescription>
            </DialogHeader>
            <OrgForm onSaved={() => setOpen(false)} />
          </DialogContent>
        </Dialog>
      </div>
      <Card>
        <CardContent className="p-4">
          <div className="relative max-w-xl">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input className="pl-9" placeholder="Search organizations" value={query} onChange={(event) => setQuery(event.target.value)} />
          </div>
        </CardContent>
      </Card>
      <div className="grid gap-3 xl:grid-cols-2">
        {orgs.isLoading ? <Skeleton className="h-80 w-full" /> : orgs.data?.items?.length ? orgs.data.items.map((org) => <OrgCard key={org.org_id} org={org} />) : <Card><CardContent className="p-8 text-center text-sm text-muted-foreground">No organizations match this view.</CardContent></Card>}
      </div>
    </div>
  );
}

export function OrgDetailRoute() {
  const { id } = useParams({ from: "/app/orgs/$id" });
  const org = useOrganization(id);
  if (org.isLoading) return <Skeleton className="h-[620px] w-full" />;
  if (!org.data) return <Card><CardContent className="p-8">Organization not found.</CardContent></Card>;
  const data = org.data.organization;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{String(data.display_name ?? data.legal_name)}</h1>
        <p className="text-sm text-muted-foreground">{[data.kind, data.domain, data.industry].filter(Boolean).join(" · ")}</p>
      </div>
      <Tabs defaultValue="overview">
        <TabsList className="flex h-auto flex-wrap">
          {["overview", "people", "hierarchy", "tags", "audit"].map((tab) => <TabsTrigger key={tab} value={tab}>{tab}</TabsTrigger>)}
        </TabsList>
        <TabsContent value="overview" className="grid gap-4 lg:grid-cols-2">
          <ContactMethodList title="Identifiers" items={[]} />
          <ContactMethodList title="Addresses and URLs" items={[]} />
        </TabsContent>
        <TabsContent value="people"><Card><CardHeader><CardTitle>People</CardTitle><CardDescription>{org.data.employee_count} linked employment rows returned.</CardDescription></CardHeader></Card></TabsContent>
        <TabsContent value="hierarchy"><Card><CardHeader><CardTitle>Hierarchy</CardTitle><CardDescription>Parent and subsidiary links mount here from org_org_relation.</CardDescription></CardHeader></Card></TabsContent>
        <TabsContent value="tags"><Card><CardHeader><CardTitle>Tags and notes</CardTitle></CardHeader></Card></TabsContent>
        <TabsContent value="audit"><Card><CardHeader><CardTitle>Audit trail</CardTitle></CardHeader><CardContent><AuditList events={org.data.recent_action_events} /></CardContent></Card></TabsContent>
      </Tabs>
    </div>
  );
}
