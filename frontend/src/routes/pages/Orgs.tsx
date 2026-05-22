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
            <Button className="bg-violet-500 hover:bg-violet-600">
              <Plus className="h-4 w-4" />
              Add organization
            </Button>
          </DialogTrigger>
          <DialogContent className="w-[400px]">
            <DialogHeader>
              <DialogTitle>Add organization</DialogTitle>
              <DialogDescription>Create through create_organization.</DialogDescription>
            </DialogHeader>
            <OrgForm onSaved={() => setOpen(false)} />
          </DialogContent>
        </Dialog>
      </div>
      <Card className="border-violet-200 mb-4">
        <CardHeader>
          <h2 className="font-semibold text-violet-600">Search organizations</h2>
        </CardHeader>
        <CardContent className="p-4">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input 
              className="pl-9 w-full" 
              placeholder="Search organizations..." 
              value={query} 
              onChange={(event) => setQuery(event.target.value)} 
            />
          </div>
        </CardContent>
      </Card>
      <div className="min-w-0">
        {orgs.isLoading ? (
          <div className="space-y-4">
            <Skeleton className="h-8 w-24" />
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-4 w-12" />
            <Skeleton className="h-4 w-10" />
          </div>
        ) : orgs.data?.items?.length ? (
            <>
              {/* Table View */}
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-muted">
                  <thead className="bg-muted">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        Organization
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        Domain
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        Industry
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        Kind
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        People
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-muted">
                    {orgs.data.items.map((org) => (
                      <tr 
                        key={org.org_id} 
                        className="hover:bg-muted transition-colors cursor-pointer"
                        onClick={() => {
                          // Navigate to org detail
                          window.location.href = `/orgs/${org.org_id}`;
                        }}
                      >
                        <td className="px-6 py-4 flex items-center space-x-3">
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-violet-500/20 text-violet-500">
                            {org.display_name.split(" ")[0]?.charAt(0).toUpperCase()}
                          </div>
                          <div>
                            <p className="font-medium">{org.display_name || org.legal_name}</p>
                            <p className="text-xs text-muted-foreground">{org.domain || "No domain"}</p>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-sm text-muted-foreground">
                          {org.domain || "No domain"}
                        </td>
                        <td className="px-6 py-4 text-sm text-muted-foreground">
                          {org.industry || "No industry"}
                        </td>
                        <td className="px-6 py-4 text-sm text-muted-foreground capitalize">
                          {org.kind || "other"}
                        </td>
                        <td className="px-6 py-4 text-sm text-muted-foreground font-mono tabular-nums">
                          0
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              
              {/* Load More Button */}
              {orgs.data.items.length >= 50 && (
                <div className="mt-6 flex justify-center">
                  <Button variant="outline" size="sm">
                    Load more
                  </Button>
                </div>
              )}
            </>
          ) : (
            <div className="flex flex-col items-center text-center py-12">
              <div className="mb-6">
                <svg 
                  className="h-12 w-12 text-violet-400" 
                  fill="none" 
                  stroke="currentColor" 
                  viewBox="0 0 24 24"
                >
                  <path 
                    strokeLinecap="round" 
                    strokeLinejoin="round" 
                    strokeWidth={2} 
                    d="M12 8c-1.1 0-2 .9-2 2s1 2 2 2 2-.9 2-2-1-2-2-2zm0 12c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm0-14c2.21 0 4 1.79 4 4h-4c0-1.1.9-2 2-2z"
                  />
                </svg>
              </div>
              <h2 className="mb-4 text-xl font-semibold text-violet-600">No organizations yet</h2>
              <p className="mb-6 text-muted-foreground max-w-xl">
                Start building your directory by adding your first organization. They'll appear here once created.
              </p>
              <Button 
                className="bg-violet-500 hover:bg-violet-600"
                onClick={() => setOpen(true)}
              >
                <Plus className="mr-2 h-4 w-4" />
                Add first organization
              </Button>
            </div>
          )}
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
