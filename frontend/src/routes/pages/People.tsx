import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate, useParams } from "@tanstack/react-router";
import { Archive, Camera, Plus, Save, Search, Upload } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PersonCard } from "@/components/domain/Cards";
import { ContactMethodList, EmploymentTimeline } from "@/components/domain/Lists";
import { TagChip } from "@/components/domain/Badges";
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
            <Button>
              <Plus className="h-4 w-4" />
              Add person
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add person</DialogTitle>
              <DialogDescription>Create a canonical person through the MCP tool surface.</DialogDescription>
            </DialogHeader>
            <PersonForm onSaved={() => setOpen(false)} />
          </DialogContent>
        </Dialog>
      </div>
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Filters</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="relative">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input className="pl-9" placeholder="Search people" value={query} onChange={(event) => setQuery(event.target.value)} />
            </div>
            <div>
              <p className="mb-2 text-sm font-medium">Tags</p>
              <div className="flex flex-wrap gap-1">{tags.data?.items?.slice(0, 12).map((tag) => <TagChip key={tag.slug} label={tag.display_name ?? tag.slug} />)}</div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4" /> Has email
            </label>
          </CardContent>
        </Card>
        <div className="space-y-3">
          {people.isLoading ? <Skeleton className="h-80 w-full" /> : people.data?.items?.length ? people.data.items.map((person) => <PersonCard key={person.person_id} person={person} />) : <Card><CardContent className="p-8 text-center text-sm text-muted-foreground">No matching people yet. Add the first person or clear filters.</CardContent></Card>}
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
