import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { Archive, Building2, Camera, ChevronLeft, Mail, Network, Plus, Save, Search, Upload, UsersRound } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PersonCard, PersonGridCard } from "@/components/domain/Cards";
import { RelationshipCategoryPicker } from "@/components/domain/RelationshipCategoryPicker";
import { TagChip } from "@/components/domain/Badges";
import { ContactMethodList, EmploymentTimeline } from "@/components/domain/Lists";
import { ContactDetailsEditor } from "@/components/domain/ContactDetailsEditor";
import { PersonNamesEditor } from "@/components/domain/PersonNamesEditor";
import { StatStrip, StatTile, StatTileSkeleton } from "@/components/domain/Stats";
import { ViewToggle, type DirectoryLayout } from "@/components/domain/ViewToggle";
import { useAllPeople, useMcpMutation, usePerson, usePhotos, useTags } from "@/hooks/useMcp";
import { QueryErrorState } from "@/components/dashboard";
import { isRenderableQueryError } from "@/lib/queryState";
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
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const people = useAllPeople({ query: query.trim() || undefined });
  const tags = useTags();

  // Walk every page so the directory shows ALL people (grouped A→Z), not a
  // truncated first 50. ~823 contacts ≈ 9 pages of 100; React Query caches them.
  // Precise member deps are intentional — depending on the whole `people` query
  // object would re-run this on every query-state identity change.
  useEffect(() => {
    if (people.hasNextPage && !people.isFetchingNextPage) void people.fetchNextPage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [people.hasNextPage, people.isFetchingNextPage, people.data, people.fetchNextPage]);

  const items = useMemo(
    () => people.data?.pages.flatMap((p) => p.items ?? []) ?? [],
    [people.data]
  );
  const visible = hasEmailOnly ? items.filter((person) => Boolean(person.primary_email)) : items;
  const tagItems = useMemo(() => tags.data?.items ?? [], [tags.data]);
  // Fold case/format variants (e.g. "Friends" + "friends") so each tag appears
  // once in the filter. The data may still hold the variants until Data Quality
  // → Canonicalize tags is run; this just keeps the list clean. The most-used
  // variant's label wins, and folding by slug also fixes the duplicate React key.
  const foldedTags = useMemo(() => {
    const byKey = new Map<string, (typeof tagItems)[number]>();
    for (const t of tagItems) {
      const key = (t.slug || t.display_name || "").toLowerCase();
      if (!key) continue;
      const prev = byKey.get(key);
      if (!prev || (t.usage_count ?? 0) > (prev.usage_count ?? 0)) byKey.set(key, t);
    }
    return [...byKey.values()];
  }, [tagItems]);
  const filtered = Boolean(query) || hasEmailOnly;

  const totalPeople = people.data?.pages?.[0]?.total_count ?? items.length;
  const loadingAll = people.isLoading || people.hasNextPage || people.isFetchingNextPage;
  const withEmail = useMemo(() => items.filter((p) => Boolean(p.primary_email)).length, [items]);
  const orgCount = useMemo(
    () => new Set(items.map((p) => p.current_org_id ?? p.current_org).filter(Boolean)).size,
    [items]
  );

  // A→Z section grouping — the alphabetic delimiters. Server sorts by
  // display_name; we bucket by first letter (non-alpha → "#") and keep "#" last.
  const sections = useMemo(() => {
    const groups = new Map<string, typeof visible>();
    for (const p of visible) {
      const first = (p.display_name || "").trim().charAt(0).toUpperCase();
      const letter = first >= "A" && first <= "Z" ? first : "#";
      const arr = groups.get(letter);
      if (arr) arr.push(p);
      else groups.set(letter, [p]);
    }
    return [...groups.entries()].sort(([a], [b]) =>
      a === "#" ? 1 : b === "#" ? -1 : a.localeCompare(b)
    );
  }, [visible]);
  const presentLetters = useMemo(() => new Set(sections.map(([l]) => l)), [sections]);
  const ALPHABET = useMemo(() => "ABCDEFGHIJKLMNOPQRSTUVWXYZ#".split(""), []);

  function jumpTo(letter: string) {
    scrollRef.current
      ?.querySelector(`[data-letter="${letter}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

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
          <StatTile label="With email" value={withEmail} icon={Mail} tone="sky" />
          <StatTile label="Organizations" value={orgCount} icon={Building2} tone="emerald" />
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
              {foldedTags.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {foldedTags.slice(0, 12).map((tag) => (
                    <TagChip key={tag.slug} label={tag.display_name ?? tag.slug} />
                  ))}
                  {foldedTags.length > 12 ? (
                    <span className="text-xs text-muted-foreground">+{foldedTags.length - 12} more</span>
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
                  {loadingAll && !people.isLoading ? (
                    <span className="ml-1.5 text-xs text-muted-foreground/80">· loading the rest…</span>
                  ) : null}
                </>
              )}
            </p>
            <ViewToggle value={layout} onChange={setLayout} />
          </div>

          {people.isLoading ? (
            <div className="space-y-2.5">
              {Array.from({ length: 6 }).map((_, index) => (
                <PersonCardSkeleton key={index} />
              ))}
            </div>
          ) : isRenderableQueryError(people) ? (
            // A backend failure must not masquerade as "No people yet".
            <QueryErrorState
              title="Couldn't load people"
              error={people.error}
              onRetry={() => void people.refetch()}
              retrying={people.isRefetching}
            />
          ) : visible.length ? (
            <div className="flex gap-3">
              {/* A→Z directory with alphabetic section delimiters */}
              <div ref={scrollRef} className="min-w-0 flex-1 space-y-5">
                {sections.map(([letter, members]) => (
                  <section key={letter} data-letter={letter} className="scroll-mt-24 space-y-2.5">
                    <div className="sticky top-16 z-10 flex items-center gap-3 bg-background/85 py-1.5 backdrop-blur">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-[oklch(var(--co-brand-300)/0.3)] bg-[oklch(var(--co-brand-500)/0.1)] font-mono text-xs font-semibold text-[oklch(var(--co-brand-300))]">
                        {letter}
                      </span>
                      <span className="h-px flex-1 bg-border" />
                      <span className="co-mono-numeric text-xs text-muted-foreground">{members.length}</span>
                    </div>
                    <div className={cn(layout === "grid" ? "grid gap-3 sm:grid-cols-2 xl:grid-cols-3" : "space-y-2.5")}>
                      {members.map((person) =>
                        layout === "grid" ? (
                          <PersonGridCard key={person.person_id} person={person} />
                        ) : (
                          <PersonCard key={person.person_id} person={person} />
                        )
                      )}
                    </div>
                  </section>
                ))}
              </div>

              {/* A→Z jump rail */}
              <nav
                aria-label="Jump to letter"
                className="sticky top-20 hidden h-fit shrink-0 flex-col items-center gap-px self-start md:flex"
              >
                {ALPHABET.map((letter) => {
                  const has = presentLetters.has(letter);
                  return (
                    <button
                      key={letter}
                      type="button"
                      disabled={!has}
                      onClick={() => jumpTo(letter)}
                      className={cn(
                        "focus-ring h-4 w-5 rounded text-[10px] font-medium leading-4 transition-colors",
                        has
                          ? "text-muted-foreground hover:bg-[oklch(var(--co-brand-500)/0.12)] hover:text-[oklch(var(--co-brand-300))]"
                          : "cursor-default text-muted-foreground/25"
                      )}
                      aria-label={`Jump to ${letter}`}
                    >
                      {letter}
                    </button>
                  );
                })}
              </nav>
            </div>
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
  const setCategory = useMcpMutation(tools.setRelationshipCategory);

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
          <RelationshipCategoryPicker
            value={data.relationship_category}
            busy={setCategory.isPending}
            onSelect={(category) => void setCategory.mutateAsync({ person_id: id, category })}
          />
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

        <TabsContent value="overview" className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <ContactMethodList title="Emails" items={data.emails} />
            <ContactMethodList title="Phones" items={data.phones} />
            <ContactMethodList title="Addresses" items={[]} />
          </div>
          <ContactDetailsEditor
            personId={id}
            data={{
              im_handles: data.im_handles,
              identifiers: data.identifiers,
              urls: data.urls
            }}
          />
          <PersonNamesEditor key={id} personId={id} />
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
