import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { Archive, Briefcase, Building2, CalendarDays, ChevronLeft, Globe, MapPin, Plus, Save, Search, UsersRound } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { OrgCard, OrgGridCard, PersonCard } from "@/components/domain/Cards";
import { ContactMethodList } from "@/components/domain/Lists";
import { StatStrip, StatTile, StatTileSkeleton } from "@/components/domain/Stats";
import { ViewToggle, type DirectoryLayout } from "@/components/domain/ViewToggle";
import { useMcpMutation, useOrganization, useOrganizations, usePeople } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import type { OrganizationSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AuditList } from "@/routes";

const orgSchema = z.object({
  legal_name: z.string().min(1),
  display_name: z.string().optional(),
  domain: z.string().optional(),
  industry: z.string().optional()
});

function FieldLabel({ htmlFor, children }: { htmlFor: string; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
      {children}
    </label>
  );
}

function OrgForm({ onSaved }: { onSaved: () => void }) {
  const {
    register,
    handleSubmit,
    formState: { errors }
  } = useForm<z.infer<typeof orgSchema>>({ resolver: zodResolver(orgSchema), defaultValues: { legal_name: "" } });
  const create = useMcpMutation(tools.createOrganization);

  return (
    <form
      className="space-y-4"
      onSubmit={handleSubmit(async (values) => {
        await create.mutateAsync({
          legal_name: values.legal_name,
          display_name: values.display_name || undefined,
          domain: values.domain || undefined,
          industry: values.industry || undefined
        });
        onSaved();
      })}
    >
      <div className="space-y-1.5">
        <FieldLabel htmlFor="legal_name">Legal name</FieldLabel>
        <Input id="legal_name" placeholder="Analytical Engine Company" {...register("legal_name")} />
        {errors.legal_name ? <p className="text-xs text-destructive">A legal name is required.</p> : null}
      </div>
      <div className="space-y-1.5">
        <FieldLabel htmlFor="display_name">Display name</FieldLabel>
        <Input id="display_name" placeholder="Analytical Engine Co." {...register("display_name")} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <FieldLabel htmlFor="domain">Domain</FieldLabel>
          <Input id="domain" placeholder="analytical-engine.org" {...register("domain")} />
        </div>
        <div className="space-y-1.5">
          <FieldLabel htmlFor="industry">Industry</FieldLabel>
          <Input id="industry" placeholder="Computing" {...register("industry")} />
        </div>
      </div>
      <div className="flex justify-end pt-1">
        <Button type="submit" disabled={create.isPending}>
          <Save className="h-4 w-4" strokeWidth={1.6} />
          {create.isPending ? "Creating…" : "Create organization"}
        </Button>
      </div>
    </form>
  );
}

function AddOrgDialog({
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
            Add organization
          </Button>
        </DialogTrigger>
      )}
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add organization</DialogTitle>
          <DialogDescription>Create a canonical organization through the MCP tool surface.</DialogDescription>
        </DialogHeader>
        <OrgForm onSaved={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function OrgCardSkeleton() {
  return (
    <Card className="border-border">
      <CardContent className="flex items-center gap-4 p-4">
        <Skeleton className="h-11 w-11 rounded-full" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-44" />
          <Skeleton className="h-3 w-60" />
        </div>
        <Skeleton className="hidden h-3 w-4 sm:block" />
      </CardContent>
    </Card>
  );
}

function EmptyOrgs({ filtered, onAdd }: { filtered: boolean; onAdd: () => void }) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-4 py-16 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
          <Building2 className="h-6 w-6" strokeWidth={1.5} />
        </div>
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">{filtered ? "No matches" : "No organizations yet"}</h2>
          <p className="mx-auto max-w-md text-sm text-muted-foreground">
            {filtered
              ? "No organizations match the current filters. Try clearing the search or the has-domain filter."
              : "Start building your directory by adding your first organization. They'll appear here once created."}
          </p>
        </div>
        {!filtered ? (
          <Button onClick={onAdd}>
            <Plus className="h-4 w-4" strokeWidth={1.6} />
            Add first organization
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function OrgsRoute() {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [hasDomainOnly, setHasDomainOnly] = useState(false);
  const [layout, setLayout] = useState<DirectoryLayout>("list");
  const args = useMemo(() => ({ query: query || undefined, limit: 50 }), [query]);
  const orgs = useOrganizations(args);

  const items: OrganizationSummary[] = useMemo(() => orgs.data?.items ?? [], [orgs.data]);
  const visible = hasDomainOnly ? items.filter((org) => Boolean(org.domain)) : items;
  const count = orgs.data?.count ?? items.length;
  const filtered = Boolean(query) || hasDomainOnly;
  // Distinct industries across the loaded page, for the calm overview rail.
  const industries = Array.from(new Set(items.map((org) => org.industry).filter(Boolean) as string[])).slice(0, 12);

  // Loaded-page overview metrics for the directory stat strip.
  const totalOrgs = orgs.data?.total_count ?? count;
  const withDomain = useMemo(() => items.filter((o) => Boolean(o.domain)).length, [items]);
  const industryCount = useMemo(
    () => new Set(items.map((o) => o.industry).filter(Boolean)).size,
    [items]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl font-semibold tracking-tight">Organizations</h1>
            {!orgs.isLoading && orgs.data ? (
              <span className="co-mono-numeric rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                {totalOrgs}
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Browse, search, and curate canonical companies, nonprofits, agencies, and groups.
          </p>
        </div>
        <Button variant="gradient" className="h-9" onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" strokeWidth={1.8} />
          Add organization
        </Button>
        <AddOrgDialog open={open} onOpenChange={setOpen} hideTrigger />
      </div>

      {/* Overview stat strip */}
      {orgs.isLoading ? (
        <StatStrip>
          <StatTileSkeleton />
          <StatTileSkeleton />
          <StatTileSkeleton />
        </StatStrip>
      ) : items.length ? (
        <StatStrip>
          <StatTile label="Organizations" value={totalOrgs} icon={Building2} tone="emerald" />
          <StatTile label="With domain" value={withDomain} icon={Globe} tone="sky" hint="verified web presence" />
          <StatTile label="Industries" value={industryCount} icon={Briefcase} tone="amber" hint="across this page" />
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
                placeholder="Search organizations…"
                aria-label="Search organizations"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>

            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Industries</p>
              {industries.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {industries.map((industry) => (
                    <span
                      key={industry}
                      className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-muted/70 px-2 py-0.5 text-xs font-medium text-foreground/80"
                    >
                      <Briefcase className="h-3 w-3 shrink-0 text-muted-foreground" strokeWidth={1.6} />
                      <span className="truncate">{industry}</span>
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No industries yet.</p>
              )}
            </div>

            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Refine</p>
              <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground/90">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-border accent-primary"
                  checked={hasDomainOnly}
                  onChange={(event) => setHasDomainOnly(event.target.checked)}
                />
                Has domain
              </label>
            </div>
          </CardContent>
        </Card>

        {/* List */}
        <div className="min-w-0 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground" aria-live="polite">
              {orgs.isLoading ? "Loading…" : (
                <>
                  <span className="co-mono-numeric font-medium text-foreground">{visible.length}</span>{" "}
                  {visible.length === 1 ? "organization" : "organizations"}
                  {filtered ? " match" : ""}
                </>
              )}
            </p>
            <ViewToggle value={layout} onChange={setLayout} />
          </div>

          {orgs.isLoading ? (
            layout === "grid" ? (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {Array.from({ length: 6 }).map((_, index) => (
                  <Skeleton key={index} className="h-[172px] w-full rounded-xl" />
                ))}
              </div>
            ) : (
              <div className="space-y-2.5">
                {Array.from({ length: 5 }).map((_, index) => (
                  <OrgCardSkeleton key={index} />
                ))}
              </div>
            )
          ) : visible.length ? (
            <>
              <div className={cn(layout === "grid" ? "grid gap-3 sm:grid-cols-2 xl:grid-cols-3" : "space-y-2.5")}>
                {visible.map((org) =>
                  layout === "grid" ? (
                    <OrgGridCard key={org.org_id} org={org} />
                  ) : (
                    <OrgCard key={org.org_id} org={org} />
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
            <EmptyOrgs filtered={filtered} onAdd={() => setOpen(true)} />
          )}
        </div>
      </div>
    </div>
  );
}

function OrgDetailSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-4 w-28" />
      <div className="flex items-center gap-4">
        <Skeleton className="h-16 w-16 rounded-full" />
        <div className="space-y-2">
          <Skeleton className="h-7 w-56" />
          <Skeleton className="h-4 w-72" />
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

/** A single org-profile fact: an icon, a label, and a value (or a graceful blank). */
function OrgFact({
  icon: Icon,
  label,
  value,
  href
}: {
  icon: typeof Building2;
  label: string;
  value?: React.ReactNode;
  href?: string;
}) {
  const empty = value === null || value === undefined || value === "";
  return (
    <div className="flex items-start gap-3 py-2.5">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={1.6} />
      <div className="min-w-0">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
        {empty ? (
          <p className="text-sm text-muted-foreground/70">Not recorded</p>
        ) : href ? (
          // text-foreground (not text-primary) keeps AA contrast in dark mode, where the
          // darkened brand-500 falls below 4.5:1 as small text. Underline marks the link;
          // brand only appears on hover.
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="focus-ring inline-block max-w-full truncate rounded text-sm font-medium text-foreground underline decoration-border underline-offset-2 transition-colors hover:text-link hover:decoration-link"
          >
            {value}
          </a>
        ) : (
          <p className="truncate text-sm font-medium text-foreground">{value}</p>
        )}
      </div>
    </div>
  );
}

const DETAIL_TABS = ["overview", "people", "hierarchy", "tags", "audit"] as const;

export function OrgDetailRoute() {
  const { id } = useParams({ from: "/app/orgs/$id" });
  const navigate = useNavigate();
  const org = useOrganization(id);
  const employees = usePeople({ current_org_id: id, limit: 50 });
  const archive = useMcpMutation(tools.archiveOrganization);

  if (org.isLoading) return <OrgDetailSkeleton />;
  if (!org.data)
    return (
      <Card>
        <CardContent className="p-8 text-sm text-muted-foreground">Organization not found.</CardContent>
      </Card>
    );

  const data = org.data.organization;
  const name = String(data.display_name ?? data.legal_name ?? "Organization");
  const subtitle = [data.kind, data.domain, data.industry].filter(Boolean).join(" · ");
  // HQ / founded are not always surfaced by get_organization today; read defensively.
  const hq = (data.headquarters ?? data.hq_address ?? data.primary_address) as string | undefined;
  const founded = (data.founded_year ?? data.founded) as number | string | undefined;
  const employeeCount = org.data.employee_count ?? 0;
  const employeeItems = employees.data?.items ?? [];

  return (
    <div className="space-y-6">
      <Link
        to="/orgs"
        className="focus-ring inline-flex items-center gap-1 rounded text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronLeft className="h-4 w-4" strokeWidth={1.6} />
        Organizations
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-primary/12 text-primary">
            <Building2 className="h-7 w-7" strokeWidth={1.5} />
          </div>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{name}</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">{subtitle || "No details yet"}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={archive.isPending}
            onClick={() => void archive.mutateAsync({ org_id: id, reason: "other" }).then(() => navigate({ to: "/orgs" }))}
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
          <Card>
            <CardHeader>
              <CardTitle>Organization profile</CardTitle>
              <CardDescription>Canonical firmographic facts for this organization.</CardDescription>
            </CardHeader>
            <CardContent className="divide-y divide-border py-0">
              <OrgFact icon={Briefcase} label="Industry" value={data.industry as string | undefined} />
              <OrgFact
                icon={Globe}
                label="Domain"
                value={data.domain as string | undefined}
                href={data.domain ? `https://${String(data.domain)}` : undefined}
              />
              <OrgFact icon={Building2} label="Kind" value={data.kind ? String(data.kind) : undefined} />
              <OrgFact icon={MapPin} label="Headquarters" value={hq} />
              <OrgFact icon={CalendarDays} label="Founded" value={founded != null ? String(founded) : undefined} />
              <OrgFact
                icon={UsersRound}
                label="People"
                value={
                  <span className="co-mono-numeric">
                    {employeeCount} {employeeCount === 1 ? "person" : "people"}
                  </span>
                }
              />
            </CardContent>
          </Card>
          <div className="grid gap-4">
            <ContactMethodList title="Identifiers and URLs" items={[]} />
            <ContactMethodList title="Addresses" items={[]} />
          </div>
        </TabsContent>

        <TabsContent value="people">
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div className="space-y-1.5">
                <CardTitle>People</CardTitle>
                <CardDescription>People with a current role at {name}.</CardDescription>
              </div>
              <span className="co-mono-numeric shrink-0 rounded-full border border-border bg-muted px-2.5 py-0.5 text-sm text-muted-foreground">
                {employeeCount}
              </span>
            </CardHeader>
            <CardContent>
              {employees.isLoading ? (
                <div className="space-y-2.5">
                  {Array.from({ length: 3 }).map((_, index) => (
                    <OrgCardSkeleton key={index} />
                  ))}
                </div>
              ) : employeeItems.length ? (
                <div className="space-y-2.5">
                  {employeeItems.map((person) => (
                    <PersonCard key={person.person_id} person={person} />
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
                  <UsersRound className="h-8 w-8" strokeWidth={1.5} />
                  <p className="text-sm">No people are linked to this organization yet.</p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="hierarchy">
          <Card>
            <CardHeader>
              <CardTitle>Hierarchy</CardTitle>
              <CardDescription>
                Parent and subsidiary links mount here from org_org_relation.
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

        <TabsContent value="tags">
          <Card>
            <CardHeader>
              <CardTitle>Tags and notes</CardTitle>
              <CardDescription>Per-tenant notes, visible only inside this workspace.</CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea
                defaultValue={(data.description as string | undefined) ?? ""}
                placeholder="Add a note about this organization…"
              />
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
              <AuditList events={org.data.recent_action_events} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
