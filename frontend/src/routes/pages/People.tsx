import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate, useParams } from "@tanstack/react-router";
import { formatDistanceToNow } from "date-fns";
import { Archive, Camera, Plus, Save, Search, Upload } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TagChip } from "@/components/domain/Badges";
import { ContactMethodList, EmploymentTimeline } from "@/components/domain/Lists";
import { useMcpMutation, usePeople, usePerson, usePhotos, useTags } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import { initials } from "@/lib/utils";
import { AuditList } from "@/routes";

const personSchema = z.object({
  display_name: z.string().min(1),
  given_name: z.string().optional(),
  family_name: z.string().optional(),
  pronouns: z.string().optional(),
  headline: z.string().optional(),
  primary_email: z.string().email().optional().or(z.literal(""))
});

function PersonForm({ onSaved }: { onSaved: () => void }) {
  const form = useForm<z.infer<typeof personSchema>>({ resolver: zodResolver(personSchema), defaultValues: { display_name: "" } });
  const create = useMcpMutation(tools.createPerson);
  return (
    <form
      className="space-y-3"
      onSubmit={form.handleSubmit(async (values) => {
        await create.mutateAsync({
          display_name: values.display_name,
          given_name: values.given_name || undefined,
          family_name: values.family_name || undefined,
          pronouns: values.pronouns || undefined,
          headline: values.headline || undefined,
          emails: values.primary_email ? [{ address: values.primary_email, type: "work", is_primary: true }] : []
        });
        onSaved();
      })}
    >
      <Input placeholder="Display name" {...form.register("display_name")} />
      <div className="grid gap-3 sm:grid-cols-2">
        <Input placeholder="Given name" {...form.register("given_name")} />
        <Input placeholder="Family name" {...form.register("family_name")} />
      </div>
      <Input placeholder="Primary email" {...form.register("primary_email")} />
      <Input placeholder="Pronouns" {...form.register("pronouns")} />
      <Input placeholder="Headline" {...form.register("headline")} />
      <Button type="submit" disabled={create.isPending}>
        <Save className="h-4 w-4" />
        Create person
      </Button>
    </form>
  );
}

export function PeopleRoute() {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const args = useMemo(() => ({ query: query || undefined, limit: 50, include_archived: false }), [query]);
  const people = usePeople(args);
  const tags = useTags();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">People</h1>
          <p className="text-sm text-muted-foreground">Search, filter, create, and curate canonical people.</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button className="bg-violet-500 hover:bg-violet-600">
              <Plus className="h-4 w-4" />
              Add person
            </Button>
          </DialogTrigger>
          <DialogContent className="w-[400px]">
            <DialogHeader>
              <DialogTitle>Add person</DialogTitle>
              <DialogDescription>Create a canonical person through the MCP tool surface.</DialogDescription>
            </DialogHeader>
            <PersonForm onSaved={() => setOpen(false)} />
          </DialogContent>
        </Dialog>
      </div>
      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        {/* Filter Sidebar */}
        <Card className="border-violet-200">
          <CardHeader>
            <h2 className="font-semibold text-violet-600">Filters</h2>
          </CardHeader>
          <CardContent className="space-y-4 pb-4">
            {/* Search */}
            <div className="relative mb-4">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input 
                className="pl-9 w-full" 
                placeholder="Search people..." 
                value={query} 
                onChange={(event) => setQuery(event.target.value)} 
              />
            </div>
            
            {/* Tags */}
            <div>
              <p className="mb-2 text-sm font-medium text-violet-600">Tags</p>
              <div className="flex flex-wrap gap-1 mb-3">
                {tags.data?.items?.slice(0, 12).map((tag) => (
                  <TagChip 
                    key={tag.slug} 
                    label={tag.display_name ?? tag.slug}
                  />
                ))}
              </div>
              {tags.data?.items != null && tags.data.items.length > 12 && (
                <p className="text-xs text-muted-foreground italic">
                  +{tags.data.items.length - 12} more
                </p>
              )}
            </div>
            
            {/* Has email filter */}
            <label className="flex items-center gap-2 text-sm">
              <input 
                type="checkbox" 
                className="h-4 w-4 text-violet-500" 
              />
              Has email
            </label>
          </CardContent>
        </Card>
        
        {/* People List */}
        <div className="min-w-0">
          {people.isLoading ? (
            <div className="space-y-4">
              <Skeleton className="h-8 w-24" />
              <Skeleton className="h-4 w-16" />
              <Skeleton className="h-4 w-12" />
              <Skeleton className="h-4 w-10" />
            </div>
          ) : people.data?.items?.length ? (
            <>
              {/* Table View */}
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-muted">
                  <thead className="bg-muted">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        Person
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        Email
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        Organization
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        Last Interaction
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        Tags
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-muted">
                    {people.data.items.map((person) => (
                      <tr 
                        key={person.person_id} 
                        className="hover:bg-muted transition-colors cursor-pointer"
                        onClick={() => {
                          // Navigate to person detail
                          window.location.href = `/people/${person.person_id}`;
                        }}
                      >
                        <td className="px-6 py-4 flex items-center space-x-3">
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-violet-500/20 text-violet-500">
                            {person.display_name.split(" ")[0]?.charAt(0).toUpperCase()}
                          </div>
                          <div>
                            <p className="font-medium">{person.display_name}</p>
                            {person.relationship_temperature && (
                              <Badge 
                                variant="secondary" 
                                className="text-xs ml-1"
                              >
                                {person.relationship_temperature}
                              </Badge>
                            )}
                          </div>
                        </td>
                        <td className="px-6 py-4 text-sm text-muted-foreground">
                          {person.primary_email || "No email"}
                        </td>
                        <td className="px-6 py-4 text-sm text-muted-foreground">
                          {person.current_org || "No organization"}
                        </td>
                        <td className="px-6 py-4 text-sm text-muted-foreground font-mono tabular-nums">
                          {person.last_interaction_at ? (
                            <span title={new Date(person.last_interaction_at).toLocaleString()}>
                              {formatDistanceToNow(new Date(person.last_interaction_at), { addSuffix: true })}
                            </span>
                          ) : (
                            "Never"
                          )}
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex flex-wrap gap-1 text-xs">
                            {person.tags?.slice(0, 3).map((tag) => (
                              <Badge 
                                key={tag} 
                                variant="outline" 
                              >
                                {tag}
                              </Badge>
                            ))}
                            {person.tags != null && person.tags.length > 3 && (
                              <Badge variant="outline" className="ml-1">
                                +{person.tags.length - 3} more
                              </Badge>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              
              {/* Load More Button */}
              {people.data.items.length >= 50 && (
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
              <h2 className="mb-4 text-xl font-semibold text-violet-600">No people yet</h2>
              <p className="mb-6 text-muted-foreground max-w-xl">
                Start building your network by adding your first person. They'll appear here once created.
              </p>
              <Button 
                className="bg-violet-500 hover:bg-violet-600"
                onClick={() => setOpen(true)}
              >
                <Plus className="mr-2 h-4 w-4" />
                Add first person
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function PersonDetailRoute() {
  const { id } = useParams({ from: "/app/people/$id" });
  const navigate = useNavigate();
  const person = usePerson(id);
  const photos = usePhotos(id);
  const archive = useMcpMutation(tools.archivePerson);

  if (person.isLoading) return <Skeleton className="h-[640px] w-full" />;
  if (!person.data) return <Card><CardContent className="p-8">Person not found.</CardContent></Card>;

  const data = person.data;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <button className="flex h-16 w-16 items-center justify-center rounded-full bg-muted text-lg font-semibold" title="Upload avatar">
            {initials(data.display_name)}
          </button>
          <div>
            <h1 className="text-2xl font-semibold">{data.display_name}</h1>
            <p className="text-sm text-muted-foreground">{[data.pronouns, data.occupation_title, data.headline].filter(Boolean).join(" · ")}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void archive.mutateAsync({ person_id: id, reason: "other" }).then(() => navigate({ to: "/people" }))}>
            <Archive className="h-4 w-4" />
            Archive
          </Button>
        </div>
      </div>
      <Tabs defaultValue="overview">
        <TabsList className="flex h-auto flex-wrap">
          {["overview", "relationships", "history", "tags", "photos", "voice", "audit"].map((tab) => <TabsTrigger key={tab} value={tab}>{tab}</TabsTrigger>)}
        </TabsList>
        <TabsContent value="overview" className="grid gap-4 lg:grid-cols-2">
          <ContactMethodList title="Emails" items={data.emails} />
          <ContactMethodList title="Phones" items={data.phones} />
          <ContactMethodList title="Addresses" items={[]} />
          <ContactMethodList title="Identifiers and URLs" items={[]} />
        </TabsContent>
        <TabsContent value="relationships"><Card><CardHeader><CardTitle>Relationships</CardTitle><CardDescription>Graph view lands later; list management is ready for link_relationship.</CardDescription></CardHeader><CardContent><Button variant="outline">Add relationship</Button></CardContent></Card></TabsContent>
        <TabsContent value="history"><EmploymentTimeline /></TabsContent>
        <TabsContent value="tags"><Card><CardHeader><CardTitle>Tags and notes</CardTitle></CardHeader><CardContent><Textarea defaultValue={data.bio ?? ""} placeholder="Per-tenant notes" /></CardContent></Card></TabsContent>
        <TabsContent value="photos"><Card><CardHeader><CardTitle>Photos</CardTitle></CardHeader><CardContent><Button variant="outline"><Upload className="h-4 w-4" /> Upload photo</Button><div className="mt-4 grid gap-3 sm:grid-cols-3">{photos.data?.items?.map((photo) => <img key={photo.photo_id} src={photo.download_url ?? photo.url} className="aspect-square rounded-md object-cover" />)}</div><Camera className="mt-6 h-8 w-8 text-muted-foreground" /></CardContent></Card></TabsContent>
        <TabsContent value="voice"><Card><CardHeader><CardTitle>Voice samples</CardTitle><CardDescription>Upload and speaker matching are wired to MCP tools when samples exist.</CardDescription></CardHeader><CardContent><Button variant="outline">Find matching speaker</Button></CardContent></Card></TabsContent>
        <TabsContent value="audit"><Card><CardHeader><CardTitle>Audit trail</CardTitle></CardHeader><CardContent><AuditList events={data.recent_events} /></CardContent></Card></TabsContent>
      </Tabs>
    </div>
  );
}
