import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { Archive, Building2, Camera, ChevronLeft, Mail, Network, Plus, Save, Search, Upload, UsersRound } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PersonCard, PersonGridCard } from "@/components/domain/Cards";
import { TagChip } from "@/components/domain/Badges";
import { ContactMethodList, EmploymentTimeline } from "@/components/domain/Lists";
import { StatStrip, StatTile, StatTileSkeleton } from "@/components/domain/Stats";
import { ViewToggle, type DirectoryLayout } from "@/components/domain/ViewToggle";
import { useMcpMutation, usePeople, usePerson, usePhotos, useTags } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import { cn, initials } from "@/lib/utils";
import { AuditList } from "@/routes";

const personSchema = z.object({
  display_name: z.string().min(1),
  given_name: z.string().optional(),
  family_name: z.string().optional(),
  pronouns: z.string().optional(),
  headline: z.string().optional(),
  primary_email: z.string().email().optional().or(z.literal(""))
});

function FieldLabel({ htmlFor, children }: { htmlFor: string; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
      {children}
    </label>
  );
}

function PersonForm({ onSaved }: { onSaved: () => void }) {
  const {
    register,
    handleSubmit,
    formState: { errors }
  } = useForm<z.infer<typeof personSchema>>({ resolver: zodResolver(personSchema), defaultValues: { display_name: "" } });
  const create = useMcpMutation(tools.createPerson);

  return (
    <form
      className="space-y-4"
      onSubmit={handleSubmit(async (values) => {
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
      <div className="space-y-1.5">
        <FieldLabel htmlFor="display_name">Display name</FieldLabel>
        <Input id="display_name" placeholder="Ada Lovelace" {...register("display_name")} />
        {errors.display_name ? <p className="text-xs text-destructive">A display name is required.</p> : null}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <FieldLabel htmlFor="given_name">Given name</FieldLabel>
          <Input id="given_name" placeholder="Ada" {...register("given_name")} />
        </div>
        <div className="space-y-1.5">
          <FieldLabel htmlFor="family_name">Family name</FieldLabel>
          <Input id="family_name" placeholder="Lovelace" {...register("family_name")} />
        </div>
      </div>
      <div className="space-y-1.5">
        <FieldLabel htmlFor="primary_email">Primary email</FieldLabel>
        <Input id="primary_email" type="email" placeholder="REMOVED" {...register("primary_email")} />
        {errors.primary_email ? <p className="text-xs text-destructive">Enter a valid email address.</p> : null}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <FieldLabel htmlFor="pronouns">Pronouns</FieldLabel>
          <Input id="pronouns" placeholder="she/her" {...register("pronouns")} />
        </div>
        <div className="space-y-1.5">
          <FieldLabel htmlFor="headline">Headline</FieldLabel>
          <Input id="headline" placeholder="Mathematician" {...register("headline")} />
        </div>
      </div>
      <div className="flex justify-end pt-1">
        <Button type="submit" disabled={create.isPending}>
          <Save className="h-4 w-4" strokeWidth={1.6} />
          {create.isPending ? "Creating…" : "Create person"}
        </Button>
      </div>
    </form>
  );
}

function AddPersonDialog({
  open,
  onOpenChange,
  hideTrigger = false
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  hideTrigger?: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {hideTrigger ? null : (
        <DialogTrigger asChild>
          <Button>
            <Plus className="h-4 w-4" strokeWidth={1.6} />
            Add person
          </Button>
        </DialogTrigger>
      )}
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add person</DialogTitle>
          <DialogDescription>Create a canonical person through the MCP tool surface.</DialogDescription>
        </DialogHeader>
        <PersonForm onSaved={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function PersonCardSkeleton() {
  return (
    <Card className="border-border">
      <CardContent className="flex items-center gap-4 p-4">
        <Skeleton className="h-11 w-11 rounded-full" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-3 w-64" />
        </div>
        <Skeleton className="hidden h-3 w-16 sm:block" />
      </CardContent>
    </Card>
  );
}

function EmptyPeople({ filtered, onAdd }: { filtered: boolean; onAdd: () => void }) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-4 py-16 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
          <UsersRound className="h-6 w-6" strokeWidth={1.5} />
        </div>
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">{filtered ? "No matches" : "No people yet"}</h2>
          <p className="mx-auto max-w-md text-sm text-muted-foreground">
            {filtered
              ? "No people match the current filters. Try clearing the search or the has-email filter."
              : "Start building your network by adding your first person. They'll appear here once created."}
          </p>
        </div>
        {!filtered ? (
          <Button onClick={onAdd}>
            <Plus className="h-4 w-4" strokeWidth={1.6} />
            Add first person
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function PeopleRoute() {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [hasEmailOnly, setHasEmailOnly] = useState(false);
  const [layout, setLayout] = useState<DirectoryLayout>("list");
  const args = useMemo(() => ({ query: query || undefined, limit: 50, include_archived: false }), [query]);
  const people = usePeople(args);
  const tags = useTags();

  const items = useMemo(() => people.data?.items ?? [], [people.data]);
  const visible = hasEmailOnly ? items.filter((person) => Boolean(person.primary_email)) : items;
  const count = people.data?.count ?? items.length;
  const tagItems = tags.data?.items ?? [];
  const filtered = Boolean(query) || hasEmailOnly;

  // Loaded-page overview metrics for the directory stat strip. Counts reflect
  // the current result set; the total-people stat prefers the server's total.
  const totalPeople = people.data?.total_count ?? count;
  const withEmail = useMemo(() => items.filter((p) => Boolean(p.primary_email)).length, [items]);
  const orgCount = useMemo(
    () => new Set(items.map((p) => p.current_org_id ?? p.current_org).filter(Boolean)).size,
    [items]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl font-semibold tracking-tight">People</h1>
            {!people.isLoading && people.data ? (
              <span className="co-mono-numeric rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                {totalPeople}
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">Search, filter, create, and curate canonical people.</p>
        </div>
        <Button variant="gradient" className="h-9" onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" strokeWidth={1.8} />
          Add person
        </Button>
        <AddPersonDialog open={open} onOpenChange={setOpen} hideTrigger />
      </div>

      {/* Overview stat strip */}
      {people.isLoading ? (
        <StatStrip>
          <StatTileSkeleton />
          <StatTileSkeleton />
          <StatTileSkeleton />
        </StatStrip>
      ) : items.length ? (
        <StatStrip>
          <StatTile label="People" value={totalPeople} icon={UsersRound} tone="fuchsia" />
          <StatTile label="With email" value={withEmail} icon={Mail} tone="sky" hint="on this page" />
          <StatTile label="Organizations" value={orgCount} icon={Building2} tone="emerald" hint="across this page" />
        </StatStrip>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        {/* Filters */}
        <Card className="lg:sticky lg:top-20 lg:self-start">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Filters</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5 pb-5">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" strokeWidth={1.6} />
              <Input
                className="pl-9"
                placeholder="Search people…"
                aria-label="Search people"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>

            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Tags</p>
              {tagItems.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {tagItems.slice(0, 12).map((tag) => (
                    <TagChip key={tag.slug} label={tag.display_name ?? tag.slug} />
                  ))}
                  {tagItems.length > 12 ? (
                    <span className="text-xs text-muted-foreground">+{tagItems.length - 12} more</span>
                  ) : null}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No tags yet.</p>
              )}
            </div>

            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Refine</p>
              <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground/90">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-border accent-primary"
                  checked={hasEmailOnly}
                  onChange={(event) => setHasEmailOnly(event.target.checked)}
                />
                Has email
              </label>
            </div>
          </CardContent>
        </Card>

        {/* List */}
        <div className="min-w-0 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground" aria-live="polite">
              {people.isLoading ? "Loading…" : (
                <>
                  <span className="co-mono-numeric font-medium text-foreground">{visible.length}</span>{" "}
                  {visible.length === 1 ? "person" : "people"}
                  {filtered ? " match" : ""}
                </>
              )}
            </p>
            <ViewToggle value={layout} onChange={setLayout} />
          </div>

          {people.isLoading ? (
            layout === "grid" ? (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {Array.from({ length: 6 }).map((_, index) => (
                  <Skeleton key={index} className="h-[188px] w-full rounded-xl" />
                ))}
              </div>
            ) : (
              <div className="space-y-2.5">
                {Array.from({ length: 5 }).map((_, index) => (
                  <PersonCardSkeleton key={index} />
                ))}
              </div>
            )
          ) : visible.length ? (
            <>
              <div
                className={cn(
                  layout === "grid"
                    ? "grid gap-3 sm:grid-cols-2 xl:grid-cols-3"
                    : "space-y-2.5"
                )}
              >
                {visible.map((person) =>
                  layout === "grid" ? (
                    <PersonGridCard key={person.person_id} person={person} />
                  ) : (
                    <PersonCard key={person.person_id} person={person} />
                  )
                )}
              </div>
              {items.length >= 50 ? (
                <p className="pt-2 text-center text-xs text-muted-foreground">
                  Showing the first 50 — refine your search to narrow results.
                </p>
              ) : null}
            </>
          ) : (
            <EmptyPeople filtered={filtered} onAdd={() => setOpen(true)} />
          )}
        </div>
      </div>
    </div>
  );
}

function PersonDetailSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-4 w-20" />
      <div className="flex items-center gap-4">
        <Skeleton className="h-16 w-16 rounded-full" />
        <div className="space-y-2">
          <Skeleton className="h-7 w-48" />
          <Skeleton className="h-4 w-64" />
        </div>
      </div>
      <Skeleton className="h-9 w-full max-w-md rounded-md" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-44 w-full rounded-lg" />
        <Skeleton className="h-44 w-full rounded-lg" />
      </div>
    </div>
  );
}

const DETAIL_TABS = ["overview", "relationships", "history", "tags", "photos", "voice", "audit"] as const;

export function PersonDetailRoute() {
  const { id } = useParams({ from: "/app/people/$id" });
  const navigate = useNavigate();
  const person = usePerson(id);
  const photos = usePhotos(id);
  const archive = useMcpMutation(tools.archivePerson);

  if (person.isLoading) return <PersonDetailSkeleton />;
  if (!person.data)
    return (
      <Card>
        <CardContent className="p-8 text-sm text-muted-foreground">Person not found.</CardContent>
      </Card>
    );

  const data = person.data;
  const subtitle = [data.pronouns, data.occupation_title, data.headline].filter(Boolean).join(" · ");

  return (
    <div className="space-y-6">
      <Link
        to="/people"
        className="focus-ring inline-flex items-center gap-1 rounded text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronLeft className="h-4 w-4" strokeWidth={1.6} />
        People
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <button
            type="button"
            className="focus-ring flex h-16 w-16 items-center justify-center rounded-full bg-primary/12 text-lg font-semibold text-primary"
            title="Upload avatar"
            aria-label="Upload avatar"
          >
            {initials(data.display_name)}
          </button>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{data.display_name}</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">{subtitle || "No headline yet"}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" asChild>
            <Link to="/people/$id/graph" params={{ id }}>
              <Network className="h-4 w-4" strokeWidth={1.6} />
              Graph
            </Link>
          </Button>
          <Button
            variant="outline"
            disabled={archive.isPending}
            onClick={() => void archive.mutateAsync({ person_id: id, reason: "other" }).then(() => navigate({ to: "/people" }))}
          >
            <Archive className="h-4 w-4" strokeWidth={1.6} />
            Archive
          </Button>
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList className="flex h-auto flex-wrap gap-1">
          {DETAIL_TABS.map((tab) => (
            <TabsTrigger key={tab} value={tab} className="capitalize">
              {tab}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="overview" className="grid gap-4 lg:grid-cols-2">
          <ContactMethodList title="Emails" items={data.emails} />
          <ContactMethodList title="Phones" items={data.phones} />
          <ContactMethodList title="Addresses" items={[]} />
          <ContactMethodList title="Identifiers and URLs" items={[]} />
        </TabsContent>

        <TabsContent value="relationships">
          <Card>
            <CardHeader>
              <CardTitle>Relationships</CardTitle>
              <CardDescription>
                Graph view lands later; list management is ready for link_relationship.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button variant="outline">
                <Plus className="h-4 w-4" strokeWidth={1.6} />
                Add relationship
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="history">
          <EmploymentTimeline />
        </TabsContent>

        <TabsContent value="tags">
          <Card>
            <CardHeader>
              <CardTitle>Tags and notes</CardTitle>
              <CardDescription>Per-tenant notes, visible only inside this workspace.</CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea defaultValue={data.bio ?? ""} placeholder="Add a note about this person…" />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="photos">
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <CardTitle>Photos</CardTitle>
              <Button variant="outline" size="sm">
                <Upload className="h-4 w-4" strokeWidth={1.6} />
                Upload photo
              </Button>
            </CardHeader>
            <CardContent>
              {photos.data?.items?.length ? (
                <div className="grid gap-3 sm:grid-cols-3">
                  {photos.data.items.map((photo) => (
                    <img
                      key={photo.photo_id}
                      src={photo.download_url ?? photo.url}
                      alt={`Photo of ${data.display_name}`}
                      className="aspect-square w-full rounded-md border border-border object-cover"
                    />
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2 py-8 text-center text-muted-foreground">
                  <Camera className="h-8 w-8" strokeWidth={1.5} />
                  <p className="text-sm">No photos uploaded yet.</p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="voice">
          <Card>
            <CardHeader>
              <CardTitle>Voice samples</CardTitle>
              <CardDescription>Upload and speaker matching are wired to MCP tools when samples exist.</CardDescription>
            </CardHeader>
            <CardContent>
              <Button variant="outline">Find matching speaker</Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="audit">
          <Card>
            <CardHeader>
              <CardTitle>Audit trail</CardTitle>
              <CardDescription>Every change records its actor, action, and time.</CardDescription>
            </CardHeader>
            <CardContent>
              <AuditList events={data.recent_events} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
