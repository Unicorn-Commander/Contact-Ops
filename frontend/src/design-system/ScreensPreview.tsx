/**
 * Auth-free, backend-free previews of the inner Contact-Ops screens
 * (People, Organizations, Tags, Tenants, Settings, Review Queue, Graph) for the
 * /design-system showcase. The real routes depend on react-oidc-context +
 * @tanstack/react-router + live MCP data; these mirror their canonical markup
 * with static demo data so the screens are screenshottable without login.
 *
 * Each preview is wrapped in {@link ScreenFrame}, a thin presentational twin of
 * the AppShell content column (sticky glass top bar + scrollable body), so the
 * screens read as real command-center pages. Where a real screen renders a
 * shared domain component with no auth/data needs (StatTile, PersonGridCard,
 * OrgGridCard, TagChip, AgentBadge, ConfidenceIndicator), the preview imports
 * the *actual* component — so what you see here is what ships.
 *
 * Keep the page-level class recipes here in sync with routes/pages/*.tsx; these
 * are presentational twins, not the source of truth. Wiring of the showcase tab
 * itself lives in Showcase.tsx (owned separately).
 */
import { useState, type ReactNode } from "react";
import {
  Bell,
  Briefcase,
  Building2,
  ChevronDown,
  ChevronRight,
  Copy,
  Globe,
  Hash,
  KeyRound,
  ListChecks,
  LogOut,
  Mail,
  Moon,
  Palette,
  Plus,
  Search,
  Shield,
  ShieldCheck,
  Tags as TagsIcon,
  Trash2,
  TrendingUp,
  UserCircle,
  Users,
  UsersRound
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { formatDistanceToNow } from "date-fns";
import { TagChip } from "@/components/domain/Badges";
import { StatStrip, StatTile } from "@/components/domain/Stats";
import { ViewToggle, type DirectoryLayout } from "@/components/domain/ViewToggle";
import {
  ActionAttribution,
  AgentBadge,
  ConfidenceIndicator,
  HumanBadge,
  KeyboardHint,
  MonoNumeric
} from "@/design-system";
import type { ThemeMode } from "@/design-system/tokens";
import type { OrganizationSummary, PersonSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

// ----------------------------------------------------------------------------
// Mock data
// ----------------------------------------------------------------------------

const demoPeople: PersonSummary[] = [
  {
    person_id: "p1",
    display_name: "Aaron Stransky",
    headline: "Founder, Magic Unicorn",
    primary_email: "REMOVED",
    current_org: "Magic Unicorn LLC",
    current_org_id: "o1",
    relationship_temperature: "warm",
    tags: ["founder", "veteran", "cto"],
    last_interaction_at: new Date(Date.now() - 2 * 864e5).toISOString()
  },
  {
    person_id: "p2",
    display_name: "Sam Okoye",
    headline: "Ops cofounder, GFL",
    primary_email: "REMOVED",
    current_org: "Brightwater Labs",
    current_org_id: "o2",
    relationship_temperature: "hot",
    tags: ["cofounder", "healthcare-it"],
    last_interaction_at: new Date(Date.now() - 6 * 36e5).toISOString()
  },
  {
    person_id: "p3",
    display_name: "Maya Hart",
    headline: "Physician, Bayview Health",
    primary_email: "REMOVED",
    current_org: "Bayview Health",
    current_org_id: "o3",
    relationship_temperature: "warm",
    tags: ["physician", "partner"],
    last_interaction_at: new Date(Date.now() - 11 * 864e5).toISOString()
  },
  {
    person_id: "p4",
    display_name: "Nate Ellison",
    headline: "IT Manager, Summit Medical",
    primary_email: null,
    current_org: "Summit Medical Center",
    current_org_id: "o4",
    relationship_temperature: "warm",
    tags: ["champion"],
    last_interaction_at: new Date(Date.now() - 4 * 864e5).toISOString()
  },
  {
    person_id: "p5",
    display_name: "Ray Holloway",
    headline: "K-12 EdTech keynote",
    primary_email: "REMOVED",
    current_org: "Holloway Media",
    current_org_id: "o5",
    relationship_temperature: "cold",
    tags: ["edtech", "creator"],
    last_interaction_at: new Date(Date.now() - 21 * 864e5).toISOString()
  },
  {
    person_id: "p6",
    display_name: "Leo Grant",
    headline: "Physician-scientist, Summit Medical",
    primary_email: "REMOVED",
    current_org: "Summit Medical Center",
    current_org_id: "o4",
    relationship_temperature: "cold",
    tags: ["research", "lead"],
    last_interaction_at: null
  }
];

const demoOrgs: OrganizationSummary[] = [
  { org_id: "o1", display_name: "Magic Unicorn LLC", legal_name: "Magic Unicorn LLC", kind: "business", domain: "magicunicorn.tech", industry: "AI Infrastructure" },
  { org_id: "o2", display_name: "Brightwater Labs", legal_name: "Brightwater Labs Inc.", kind: "business", domain: "brightwaterlabs.example", industry: "Healthcare IT" },
  { org_id: "o3", display_name: "Bayview Health", legal_name: "Bayview Health PA", kind: "business", domain: "bayviewhealth.com", industry: "Healthcare" },
  { org_id: "o4", display_name: "Summit Medical Center", legal_name: "Summit Medical Center Medical Center", kind: "agency", domain: "university-example.edu", industry: "Academic Medicine" },
  { org_id: "o5", display_name: "Holloway Media", legal_name: "Holloway Media LLC", kind: "individual", domain: null, industry: "Education" },
  { org_id: "o6", display_name: "Harbor Law", legal_name: "Harbor Law Group", kind: "business", domain: "harborlaw.com", industry: "Legal" }
];

const demoTags = [
  { slug: "founder", display_name: "Founder", usage_count: 24 },
  { slug: "veteran", display_name: "Veteran", usage_count: 8 },
  { slug: "physician", display_name: "Physician", usage_count: 17 },
  { slug: "edtech", display_name: "EdTech", usage_count: 12 },
  { slug: "investor", display_name: "Investor", usage_count: 31 },
  { slug: "creator", display_name: "Creator", usage_count: 6 },
  { slug: "healthcare-it", display_name: "Healthcare IT", usage_count: 9 },
  { slug: "champion", display_name: "Champion", usage_count: 4 },
  { slug: "research", display_name: "Research", usage_count: 14 },
  { slug: "legal", display_name: "Legal", usage_count: 3 },
  { slug: "partner", display_name: "Partner", usage_count: 11 },
  { slug: "vendor", display_name: "Vendor", usage_count: 2 }
];

const demoTenants = [
  { tenant_id: "t1", slug: "magic-unicorn", display_name: "Magic Unicorn LLC", kind: "magic_unicorn_internal", role: "owner", hipaa_mode: false, retention_class: "standard", member_count: 4 },
  { tenant_id: "t2", slug: "personal", display_name: "Aaron Stransky (personal)", kind: "personal", role: "owner", hipaa_mode: false, retention_class: "personal", member_count: 1 },
  { tenant_id: "t3", slug: "gfl", display_name: "Brightwater Labs", kind: "white_label_customer", role: "admin", hipaa_mode: true, retention_class: "hipaa-7yr", member_count: 6 },
  { tenant_id: "t4", slug: "majiks", display_name: "Majik's Studio", kind: "brand", role: "admin", hipaa_mode: false, retention_class: "standard", member_count: 3 }
];

// ----------------------------------------------------------------------------
// Shell frame (presentational twin of the AppShell content column)
// ----------------------------------------------------------------------------

function ScreenFrame({ title, theme, children }: { title: string; theme: ThemeMode; children: ReactNode }) {
  return (
    <section
      data-theme={theme}
      className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background text-foreground shadow-[var(--shadow-2)]"
    >
      <header className="co-glass sticky top-0 z-10 flex h-14 items-center gap-3 border-b px-4">
        <span className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">{title}</span>
        <Button variant="outline" className="ml-auto h-8 gap-2 px-2.5">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary/10 text-primary">
            <Building2 className="h-3.5 w-3.5" strokeWidth={1.8} />
          </span>
          <span className="hidden max-w-[140px] truncate text-sm font-medium sm:inline">Personal</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={1.8} />
        </Button>
        <Button variant="ghost" size="icon" aria-label="Notifications">
          <Bell className="h-4 w-4" strokeWidth={1.8} />
        </Button>
        <Button variant="outline" size="icon" className="h-8 w-8" aria-label="Account">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-xs font-medium text-primary-foreground">A</span>
        </Button>
      </header>
      <div className="co-scrollbar max-h-[760px] overflow-y-auto p-4 lg:p-6">{children}</div>
    </section>
  );
}

/** Canonical page header: title + count chip + sub-line + a gradient hero CTA. */
function PageHeader({
  title,
  count,
  subtitle,
  cta,
  eyebrow
}: {
  title: string;
  count?: number;
  subtitle: string;
  cta?: ReactNode;
  eyebrow?: string;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <div className="flex items-center gap-2.5">
          {eyebrow ? (
            <span className="inline-flex h-6 items-center rounded-full border border-primary/30 bg-primary/10 px-2 text-[11px] font-semibold uppercase tracking-wider text-primary">
              {eyebrow}
            </span>
          ) : null}
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {count != null ? (
            <span className="co-mono-numeric rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              {count}
            </span>
          ) : null}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
      </div>
      {cta}
    </div>
  );
}

function SearchField({ placeholder }: { placeholder: string }) {
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" strokeWidth={1.6} />
      <Input readOnly className="pl-9" placeholder={placeholder} />
    </div>
  );
}

function FiltersCard({ children }: { children: ReactNode }) {
  return (
    <Card className="self-start">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">Filters</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5 pb-5">{children}</CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------------------
// Per-screen previews
// ----------------------------------------------------------------------------

export function PeoplePreview({ theme, layout: forcedLayout }: { theme: ThemeMode; layout?: DirectoryLayout }) {
  const [layout, setLayout] = useState<DirectoryLayout>(forcedLayout ?? "list");
  return (
    <ScreenFrame title="People" theme={theme}>
      <div className="space-y-6">
        <PageHeader
          title="People"
          count={1284}
          subtitle="Search, filter, create, and curate canonical people."
          cta={
            <Button variant="gradient" className="h-9">
              <Plus className="h-4 w-4" strokeWidth={1.8} />
              Add person
            </Button>
          }
        />
        <StatStrip>
          <StatTile label="People" value={1284} icon={UsersRound} tone="fuchsia" />
          <StatTile label="With email" value={1102} icon={Mail} tone="sky" hint="reachable contacts" />
          <StatTile label="Organizations" value={206} icon={Building2} tone="emerald" hint="across this page" />
        </StatStrip>
        <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
          <FiltersCard>
            <SearchField placeholder="Search people…" />
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Tags</p>
              <div className="flex flex-wrap gap-1.5">
                {demoTags.slice(0, 6).map((t) => (
                  <TagChip key={t.slug} label={t.display_name} />
                ))}
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Refine</p>
              <label className="flex items-center gap-2 text-sm text-foreground/90">
                <input type="checkbox" className="h-4 w-4 rounded border-border accent-primary" readOnly />
                Has email
              </label>
            </div>
          </FiltersCard>
          <div className="min-w-0 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm text-muted-foreground">
                <span className="co-mono-numeric font-medium text-foreground">{demoPeople.length}</span> people
              </p>
              <ViewToggle value={layout} onChange={setLayout} />
            </div>
            {layout === "grid" ? (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {demoPeople.map((p) => (
                  <PersonGridPreview key={p.person_id} person={p} />
                ))}
              </div>
            ) : (
              <div className="space-y-2.5">
                {demoPeople.map((p) => (
                  <PersonRowPreview key={p.person_id} person={p} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </ScreenFrame>
  );
}

/** Row-card twin (PersonCard renders a router Link; this mirrors its chrome). */
function PersonRowPreview({ person }: { person: PersonSummary }) {
  return (
    <Card className="border-border">
      <CardContent className="flex items-center gap-4 p-4">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary/12 text-sm font-semibold text-primary">
          {person.display_name.split(" ").map((w) => w[0]).slice(0, 2).join("")}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate font-medium">{person.display_name}</p>
            {person.relationship_temperature ? (
              <Badge variant="secondary" className="shrink-0 capitalize">
                {person.relationship_temperature}
              </Badge>
            ) : null}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span className="inline-flex min-w-0 items-center gap-1.5">
              <Mail className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
              <span className="truncate">{person.primary_email ?? "No email"}</span>
            </span>
            <span className="inline-flex min-w-0 items-center gap-1.5">
              <Building2 className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
              <span className="truncate">{person.current_org ?? "No organization"}</span>
            </span>
          </div>
          {person.tags?.length ? (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {person.tags.slice(0, 3).map((t) => (
                <TagChip key={t} label={t} />
              ))}
            </div>
          ) : null}
        </div>
        <ChevronRight className="ml-auto h-4 w-4 shrink-0 self-start text-muted-foreground" strokeWidth={1.6} />
      </CardContent>
    </Card>
  );
}

/**
 * Router-free grid-tile twin of PersonGridCard (the real one wraps a router
 * Link, which needs a RouterProvider this standalone preview deliberately omits).
 * Mirrors PersonGridCard's exact chrome so the screenshot matches what ships.
 */
function PersonGridPreview({ person }: { person: PersonSummary }) {
  const last = person.last_interaction_at ? new Date(person.last_interaction_at) : null;
  return (
    <Card className="h-full border-border">
      <CardContent className="flex h-full flex-col items-center gap-3 p-5 text-center">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-primary/12 text-base font-semibold text-primary">
          {person.display_name.split(" ").map((w) => w[0]).slice(0, 2).join("")}
        </div>
        <div className="min-w-0 space-y-1">
          <p className="truncate font-medium">{person.display_name}</p>
          <p className="truncate text-sm text-muted-foreground">{person.headline ?? person.current_org ?? "No organization"}</p>
        </div>
        {person.relationship_temperature ? (
          <Badge variant="secondary" className="capitalize">
            {person.relationship_temperature}
          </Badge>
        ) : null}
        <div className="mt-auto w-full space-y-1.5 border-t border-border pt-3 text-left">
          <span className="inline-flex w-full min-w-0 items-center gap-1.5 text-sm text-muted-foreground">
            <Mail className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
            <span className="truncate">{person.primary_email ?? "No email"}</span>
          </span>
          {last ? (
            <span className="co-mono-numeric inline-flex w-full items-center gap-1.5 text-xs text-muted-foreground">
              <span className="truncate">Last contact {formatDistanceToNow(last, { addSuffix: true })}</span>
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

export function OrgsPreview({ theme, layout: forcedLayout }: { theme: ThemeMode; layout?: DirectoryLayout }) {
  const [layout, setLayout] = useState<DirectoryLayout>(forcedLayout ?? "grid");
  return (
    <ScreenFrame title="Organizations" theme={theme}>
      <div className="space-y-6">
        <PageHeader
          title="Organizations"
          count={206}
          subtitle="Browse, search, and curate canonical companies, nonprofits, agencies, and groups."
          cta={
            <Button variant="gradient" className="h-9">
              <Plus className="h-4 w-4" strokeWidth={1.8} />
              Add organization
            </Button>
          }
        />
        <StatStrip>
          <StatTile label="Organizations" value={206} icon={Building2} tone="emerald" />
          <StatTile label="With domain" value={184} icon={Globe} tone="sky" hint="verified web presence" />
          <StatTile label="Industries" value={28} icon={Briefcase} tone="amber" hint="across this page" />
        </StatStrip>
        <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
          <FiltersCard>
            <SearchField placeholder="Search organizations…" />
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Industries</p>
              <div className="flex flex-wrap gap-1.5">
                {["AI Infrastructure", "Healthcare", "Legal", "Education"].map((i) => (
                  <span
                    key={i}
                    className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-muted/70 px-2 py-0.5 text-xs font-medium text-foreground/80"
                  >
                    <Briefcase className="h-3 w-3 shrink-0 text-muted-foreground" strokeWidth={1.6} />
                    <span className="truncate">{i}</span>
                  </span>
                ))}
              </div>
            </div>
          </FiltersCard>
          <div className="min-w-0 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm text-muted-foreground">
                <span className="co-mono-numeric font-medium text-foreground">{demoOrgs.length}</span> organizations
              </p>
              <ViewToggle value={layout} onChange={setLayout} />
            </div>
            {layout === "grid" ? (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {demoOrgs.map((o) => (
                  <OrgGridPreview key={o.org_id} org={o} />
                ))}
              </div>
            ) : (
              <div className="space-y-2.5">
                {demoOrgs.map((o) => (
                  <OrgRowPreview key={o.org_id} org={o} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </ScreenFrame>
  );
}

function OrgRowPreview({ org }: { org: OrganizationSummary }) {
  const name = org.display_name || org.legal_name || "Untitled";
  return (
    <Card className="border-border">
      <CardContent className="flex items-center gap-4 p-4">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary/12 text-primary">
          <Building2 className="h-5 w-5" strokeWidth={1.6} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate font-medium">{name}</p>
            {org.kind ? (
              <Badge variant="secondary" className="shrink-0 capitalize">
                {org.kind}
              </Badge>
            ) : null}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span className="inline-flex min-w-0 items-center gap-1.5">
              <Globe className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
              <span className="truncate">{org.domain ?? "No domain"}</span>
            </span>
            <span className="inline-flex min-w-0 items-center gap-1.5">
              <Briefcase className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
              <span className="truncate">{org.industry ?? "No industry"}</span>
            </span>
          </div>
        </div>
        <ChevronRight className="ml-auto h-4 w-4 shrink-0 self-start text-muted-foreground" strokeWidth={1.6} />
      </CardContent>
    </Card>
  );
}

/** Router-free grid-tile twin of OrgGridCard. */
function OrgGridPreview({ org }: { org: OrganizationSummary }) {
  const name = org.display_name || org.legal_name || "Untitled";
  return (
    <Card className="h-full border-border">
      <CardContent className="flex h-full flex-col items-center gap-3 p-5 text-center">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-primary/12 text-primary">
          <Building2 className="h-5 w-5" strokeWidth={1.6} />
        </div>
        <div className="min-w-0 space-y-1">
          <p className="truncate font-medium">{name}</p>
          <p className="truncate text-sm text-muted-foreground">{org.industry ?? "No industry"}</p>
        </div>
        {org.kind ? (
          <Badge variant="secondary" className="capitalize">
            {org.kind}
          </Badge>
        ) : null}
        <div className="mt-auto w-full border-t border-border pt-3 text-left">
          <span className="inline-flex w-full min-w-0 items-center gap-1.5 text-sm text-muted-foreground">
            <Globe className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
            <span className="truncate">{org.domain ?? "No domain"}</span>
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

export function TagsPreview({ theme }: { theme: ThemeMode }) {
  const max = Math.max(...demoTags.map((t) => t.usage_count));
  const size = (n: number) => {
    const r = n / max;
    if (r > 0.75) return "text-lg";
    if (r > 0.5) return "text-base";
    if (r > 0.25) return "text-sm";
    return "text-xs";
  };
  return (
    <ScreenFrame title="Tags" theme={theme}>
      <div className="space-y-6">
        <PageHeader title="Tags" count={demoTags.length} subtitle="Browse tenant tags and jump into filtered people views." />
        <StatStrip>
          <StatTile label="Tags" value={demoTags.length} icon={TagsIcon} tone="amber" />
          <StatTile label="Total applications" value={demoTags.reduce((s, t) => s + t.usage_count, 0)} icon={Hash} tone="sky" hint="tag-to-person links" />
          <StatTile label="Most used" value={31} icon={TrendingUp} tone="emerald" hint="Investor" />
        </StatStrip>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <TagsIcon className="h-4 w-4 text-[oklch(var(--co-amber-500))]" strokeWidth={1.8} />
              Tag cloud
            </CardTitle>
            <CardDescription>Sized by usage. Select a tag to filter the people list.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-center gap-2">
              {demoTags.map((t) => (
                <span
                  key={t.slug}
                  className={cn(
                    "inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-muted/70 px-3 py-1 font-medium text-foreground/80",
                    size(t.usage_count)
                  )}
                >
                  <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
                  <span className="truncate">{t.display_name}</span>
                  <span className="co-mono-numeric ml-0.5 text-xs text-muted-foreground">{t.usage_count}</span>
                </span>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </ScreenFrame>
  );
}

export function TenantsPreview({ theme }: { theme: ThemeMode }) {
  const kindAccent: Record<string, { ring: string; dot: string }> = {
    personal: { ring: "border-info/40 bg-info/10", dot: "bg-info" },
    brand: { ring: "border-warning/40 bg-warning/10", dot: "bg-warning" },
    white_label_customer: { ring: "border-success/40 bg-success/10", dot: "bg-success" },
    magic_unicorn_internal: { ring: "border-primary/40 bg-primary/10", dot: "bg-primary" }
  };
  return (
    <ScreenFrame title="Tenants" theme={theme}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Admin"
          title="Tenants"
          count={demoTenants.length}
          subtitle="Manage tenant membership, branding, retention, and HIPAA flags."
          cta={
            <Button variant="gradient" className="h-9">
              <Plus className="h-4 w-4" strokeWidth={1.8} />
              Add tenant
            </Button>
          }
        />
        <StatStrip>
          <StatTile label="Tenants" value={demoTenants.length} icon={Building2} tone="sky" />
          <StatTile label="Members" value={demoTenants.reduce((s, t) => s + t.member_count, 0)} icon={Users} tone="emerald" hint="across all workspaces" />
          <StatTile label="HIPAA tenants" value={1} icon={ShieldCheck} tone="amber" hint="compliance-gated" />
        </StatStrip>
        <div className="grid gap-4 lg:grid-cols-2">
          {demoTenants.map((t) => {
            const accent = kindAccent[t.kind];
            return (
              <Card key={t.tenant_id} className="transition-colors hover:border-primary/30">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    {t.display_name}
                    {t.hipaa_mode ? <ShieldCheck className="h-4 w-4 text-warning" strokeWidth={1.8} /> : null}
                  </CardTitle>
                  <CardDescription className="co-mono-numeric text-xs">{t.slug}</CardDescription>
                </CardHeader>
                <CardContent className="flex flex-wrap items-center gap-2">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium capitalize text-foreground",
                      accent?.ring ?? "border-border bg-muted"
                    )}
                  >
                    <span aria-hidden className={cn("h-1.5 w-1.5 shrink-0 rounded-full", accent?.dot ?? "bg-muted-foreground")} />
                    {t.kind.replace(/_/g, " ")}
                  </span>
                  <Badge variant="secondary" className="capitalize">
                    {t.role}
                  </Badge>
                  <Badge variant="outline">{t.retention_class}</Badge>
                  {t.hipaa_mode ? (
                    <span className="inline-flex items-center gap-1 rounded-full border border-destructive/50 bg-destructive/10 px-2 py-0.5 text-xs font-semibold text-foreground">
                      <Shield className="h-3 w-3 text-destructive" strokeWidth={1.8} />
                      HIPAA
                    </span>
                  ) : null}
                  <span className="co-mono-numeric ml-auto text-xs text-muted-foreground">{t.member_count} members</span>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>
    </ScreenFrame>
  );
}

export function SettingsPreview({ theme }: { theme: ThemeMode }) {
  const dark = theme === "dark";
  return (
    <ScreenFrame title="Settings" theme={theme}>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="mt-1 text-sm text-muted-foreground">Profile, CardDAV app passwords, appearance, and session controls.</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <UserCircle className="h-4 w-4 text-[oklch(var(--co-sky-500))]" strokeWidth={1.8} />
              Profile
            </CardTitle>
            <CardDescription>Values come from your Keycloak profile claims.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="flex items-center gap-4">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-primary/12 text-xl font-semibold text-link">AS</div>
              <div className="min-w-0">
                <p className="truncate text-lg font-medium">Aaron Stransky</p>
                <p className="truncate text-sm text-muted-foreground">REMOVED</p>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                ["Name", "Aaron Stransky"],
                ["Email", "REMOVED"],
                ["Username", "aaron"],
                ["Pronouns", "he/him"]
              ].map(([label, value]) => (
                <div key={label} className="space-y-1.5">
                  <label className="block text-sm font-medium text-muted-foreground">{label}</label>
                  <Input readOnly value={value} className="bg-muted" />
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Palette className="h-4 w-4 text-[oklch(var(--co-fuchsia-500))]" strokeWidth={1.8} />
              Appearance
            </CardTitle>
            <CardDescription>Override the OS color scheme for this browser.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Moon className="h-5 w-5 text-muted-foreground" strokeWidth={1.6} />
                <span className="text-sm font-medium">{dark ? "Dark mode" : "Light mode"}</span>
              </div>
              <Button variant="outline" size="sm" className="w-24">
                {dark ? "Light" : "Dark"}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-start justify-between space-y-0">
            <div className="space-y-1.5">
              <CardTitle className="flex items-center gap-2 text-base">
                <KeyRound className="h-4 w-4 text-[oklch(var(--co-amber-500))]" strokeWidth={1.8} />
                CardDAV app passwords
              </CardTitle>
              <CardDescription>Generated passwords are shown once by the MCP tool response.</CardDescription>
            </div>
            <Button size="sm">
              <Plus className="h-4 w-4" strokeWidth={1.6} />
              Generate
            </Button>
          </CardHeader>
          <CardContent className="space-y-2">
            {[
              ["iPhone Contacts", "Created May 12, 2026 · Last used May 26, 2026"],
              ["MacBook", "Created Apr 2, 2026"]
            ].map(([label, meta]) => (
              <div key={label} className="flex items-center justify-between rounded-md border border-border p-3">
                <div className="min-w-0">
                  <p className="truncate font-medium">{label}</p>
                  <p className="co-mono-numeric text-sm text-muted-foreground">{meta}</p>
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button variant="ghost" size="icon" aria-label="Copy">
                    <Copy className="h-4 w-4" strokeWidth={1.6} />
                  </Button>
                  <Button variant="ghost" size="icon" aria-label="Revoke">
                    <Trash2 className="h-4 w-4 text-destructive" strokeWidth={1.6} />
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card className="border-destructive/30">
          <CardHeader>
            <CardTitle className="text-base text-destructive">Session</CardTitle>
            <CardDescription>End your current session.</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive">
              <LogOut className="h-4 w-4" strokeWidth={1.6} />
              Sign out
            </Button>
          </CardContent>
        </Card>
      </div>
    </ScreenFrame>
  );
}

/** Twin of the /review (Review Queue) 3-pane cockpit, statically posed. */
export function ReviewQueuePreview({ theme }: { theme: ThemeMode }) {
  return (
    <section
      data-theme={theme}
      className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background text-foreground shadow-[var(--shadow-2)]"
    >
      {/* dense triage header */}
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-background/95 px-4 py-2 shadow-[var(--shadow-1)]">
        <span className="inline-flex items-center gap-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[oklch(var(--co-rose-500)/0.14)] text-[oklch(var(--co-rose-500))]">
            <ListChecks className="h-4 w-4" strokeWidth={1.8} />
          </span>
          <h1 className="text-sm font-semibold tracking-tight">Review Queue</h1>
        </span>
        <span aria-hidden className="hidden h-5 w-px bg-border sm:block" />
        <span className="inline-flex flex-wrap items-center gap-1.5 text-[13px] text-muted-foreground">
          <MonoNumeric tone="muted">12</MonoNumeric>
          <span>today</span>
          <span>/</span>
          <MonoNumeric tone="muted">34</MonoNumeric>
          <span>approved</span>
          <span>/</span>
          <MonoNumeric tone="muted">3</MonoNumeric>
          <span>snoozed</span>
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="ghost" size="sm" className="gap-1.5">
            <span className="hidden sm:inline">Focus mode</span>
          </Button>
        </div>
      </header>

      <div className="flex h-[640px]">
        {/* nav */}
        <nav className="hidden w-48 shrink-0 border-r border-border bg-[oklch(var(--sidebar))] p-2 sm:block">
          <ul className="space-y-1">
            {[
              ["Needs Review", 12, true],
              ["Snoozed", 3, false],
              ["Recently Resolved", 0, false]
            ].map(([label, n, active]) => (
              <li key={String(label)}>
                <span
                  className={cn(
                    "flex w-full items-center justify-between rounded-md px-2.5 py-2 text-[13px]",
                    active ? "bg-primary/10 font-medium text-link" : "text-muted-foreground"
                  )}
                >
                  <span>{label}</span>
                  <Badge variant="outline" className={cn("rounded-[var(--radius-sm)] px-1.5 font-mono text-[11px]", active && "border-primary/30")}>
                    <MonoNumeric tone="muted">{n}</MonoNumeric>
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-6 px-2.5 font-mono text-[11px] font-semibold uppercase text-muted-foreground">Saved views</p>
          <p className="px-2.5 text-xs italic text-muted-foreground">(local) coming in 3.4</p>
        </nav>

        {/* list */}
        <div className="co-scrollbar w-[38%] shrink-0 space-y-2 overflow-y-auto border-r border-border p-2">
          {[
            { name: "Aaron Stransky", agent: "dedupe-agent", conf: 0.96, kind: "merge duplicate", conflict: false, focused: true },
            { name: "Bayview Health", agent: "enrichment-agent", conf: 0.88, kind: "update domain", conflict: false, focused: false },
            { name: "Sam Okoye", agent: "carddav-reconcile", conf: 0.74, kind: "add phone", conflict: true, focused: false },
            { name: "Magic Unicorn LLC", agent: "enrichment-agent", conf: 0.81, kind: "set industry", conflict: false, focused: false }
          ].map((c) => (
            <div
              key={c.name}
              className={cn(
                "rounded-xl border bg-card p-3 shadow-[var(--shadow-1)] transition-colors",
                c.focused ? "border-primary/50 ring-1 ring-primary/30" : "border-border"
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{c.name}</p>
                  <p className="mt-0.5 truncate text-xs capitalize text-muted-foreground">{c.kind}</p>
                </div>
                {c.conflict ? <Badge variant="warning">conflict</Badge> : null}
              </div>
              <div className="mt-2.5 flex items-center justify-between gap-2">
                <AgentBadge slug={c.agent} size="xs" />
                <ConfidenceIndicator value={c.conf} mode="numeric" size="sm" showLabel={false} />
              </div>
            </div>
          ))}
        </div>

        {/* detail */}
        <div className="flex min-w-0 flex-1 flex-col bg-card/55">
          <header className="flex items-start gap-2.5 border-b border-border bg-card/85 p-3">
            <AgentBadge slug="dedupe-agent" label="dedupe-agent v2.1" size="sm" />
            <div className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold">Aaron Stransky</span>
              <ActionAttribution actor={{ type: "agent", slug: "dedupe-agent" }} verb="Proposed" timestamp={Date.now() - 90000} target="T1" compact className="mt-0.5" />
            </div>
          </header>
          <div className="co-scrollbar flex-1 space-y-4 overflow-y-auto p-4">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>Confidence</span>
                <ConfidenceIndicator value={0.96} mode="numeric" showLabel size="sm" />
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div className="h-full bg-confidence-emerald" style={{ width: "96%" }} />
              </div>
            </div>
            <section>
              <p className="mb-1 font-mono text-[11px] font-medium uppercase text-muted-foreground">What the agent proposes</p>
              <p className="rounded-md border border-border bg-muted/35 p-2.5 text-[13px] italic">
                Two records share the email REMOVED and a normalized name. Merging keeps the canonical
                record and folds in the duplicate's phone and tags.
              </p>
            </section>
            <section className="space-y-2">
              <p className="font-mono text-[11px] font-medium uppercase text-muted-foreground">Structured changes</p>
              {[
                ["phone", "—", "REMOVED"],
                ["tags", "founder", "founder, veteran"]
              ].map(([field, before, after]) => (
                <div key={field} className="rounded-md border border-border bg-background/40 p-2.5 text-xs">
                  <p className="font-mono uppercase text-muted-foreground">{field}</p>
                  <div className="mt-1 flex items-center gap-2">
                    <span className="text-muted-foreground line-through">{before}</span>
                    <ChevronRight className="h-3 w-3 text-muted-foreground" />
                    <span className="font-medium text-foreground">{after}</span>
                  </div>
                </div>
              ))}
            </section>
          </div>
          <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-border bg-card/95 p-3 shadow-[var(--shadow-2)]">
            <Button variant="ghost" size="sm">
              Reject
              <KeyboardHint keys="N" label="Reject" />
            </Button>
            <Button variant="outline" size="sm">
              Snooze
            </Button>
            <Button size="sm">
              Approve
              <KeyboardHint keys="Y" label="Approve" />
            </Button>
          </footer>
        </div>
      </div>
    </section>
  );
}

/** Twin of the /graph floating-overlay control panel over the canvas. */
export function GraphPreview({ theme }: { theme: ThemeMode }) {
  return (
    <section
      data-theme={theme}
      className="relative h-[640px] overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background text-foreground shadow-[var(--shadow-2)]"
    >
      {/* faux canvas backdrop with nodes */}
      <div className="absolute inset-0">
        <svg className="h-full w-full" aria-hidden="true">
          <line x1="50%" y1="50%" x2="68%" y2="32%" className="stroke-info/50" strokeWidth={1.5} />
          <line x1="50%" y1="50%" x2="72%" y2="64%" className="stroke-info/50" strokeWidth={1.5} />
          <line x1="50%" y1="50%" x2="34%" y2="60%" className="stroke-confidence-amber/60" strokeWidth={1.5} strokeDasharray="4 3" />
          <line x1="50%" y1="50%" x2="40%" y2="30%" className="stroke-info/40" strokeWidth={1.5} />
        </svg>
        <GraphNode left="50%" top="50%" label="Aaron Stransky" root />
        <GraphNode left="68%" top="32%" label="Magic Unicorn" />
        <GraphNode left="72%" top="64%" label="Sam Okoye" />
        <GraphNode left="34%" top="60%" label="Summit Medical" />
        <GraphNode left="40%" top="30%" label="Nate Ellison" />
      </div>

      {/* floating control panel (top-left) */}
      <div className="co-scrollbar absolute left-4 top-4 z-20 w-[min(30rem,calc(100%-2rem))] space-y-3 rounded-lg border border-border bg-card/90 p-4 shadow-[var(--shadow-3)] backdrop-blur-xl">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold leading-7 tracking-tight">Graph</h1>
              <Badge variant="outline">Auto</Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              <span className="co-mono-numeric font-mono">
                <strong>5</strong> nodes / <strong>4</strong> edges
              </span>{" "}
              in the current ego network
            </p>
          </div>
        </div>
        <div className="space-y-2">
          <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Hop limit</label>
          <div className="flex gap-1.5">
            {[1, 2, 3].map((h) => (
              <span
                key={h}
                className={cn(
                  "co-mono-numeric flex h-8 w-9 items-center justify-center rounded-md border text-sm",
                  h === 1 ? "border-primary/40 bg-primary/10 text-foreground" : "border-border text-muted-foreground"
                )}
              >
                {h}
              </span>
            ))}
          </div>
        </div>
        <section className="space-y-3 border-t border-border pt-3 text-sm">
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Nodes</p>
            <div className="space-y-2 rounded-md border border-border bg-background/70 p-3">
              <div className="flex items-center gap-2">
                <HumanBadge name="person" label="Person" size="xs" />
                <span className="text-foreground">Person and org nodes carry the ego network.</span>
              </div>
              <div className="flex items-center gap-2">
                <AgentBadge slug="graph-sync-worker" label="graph sync" size="xs" />
                <span className="text-foreground">Agent nodes are rendered as explicit machine actors.</span>
              </div>
            </div>
          </div>
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Edges</p>
            <div className="space-y-2 rounded-md border border-border bg-background/70 p-3">
              <div className="flex items-center gap-2">
                <span className="h-0.5 w-8 rounded-full bg-info" aria-hidden="true" />
                <span className="text-foreground">Confirmed relationship.</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="h-0.5 w-8 border-t border-dashed border-current text-confidence-amber" aria-hidden="true" />
                <span className="text-foreground">Provisional / agent-proposed link.</span>
              </div>
            </div>
          </div>
        </section>
      </div>
    </section>
  );
}

function GraphNode({ left, top, label, root = false }: { left: string; top: string; label: string; root?: boolean }) {
  return (
    <div className="absolute -translate-x-1/2 -translate-y-1/2 flex flex-col items-center gap-1" style={{ left, top }}>
      <span
        className={cn(
          "flex items-center justify-center rounded-full border shadow-[var(--shadow-1)]",
          root
            ? "h-12 w-12 border-primary/50 bg-primary/15 text-primary"
            : "h-9 w-9 border-border bg-card text-muted-foreground"
        )}
      >
        {root ? <UsersRound className="h-5 w-5" strokeWidth={1.8} /> : <Building2 className="h-4 w-4" strokeWidth={1.8} />}
      </span>
      <span className="rounded bg-background/80 px-1.5 py-0.5 text-[11px] font-medium text-foreground backdrop-blur-sm">{label}</span>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Empty-state previews (tenants can have no data)
// ----------------------------------------------------------------------------

function EmptyCard({ icon: Icon, title, body, cta }: { icon: typeof Users; title: string; body: string; cta?: string }) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-4 py-16 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
          <Icon className="h-6 w-6" strokeWidth={1.5} />
        </div>
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="mx-auto max-w-md text-sm text-muted-foreground">{body}</p>
        </div>
        {cta ? (
          <Button>
            <Plus className="h-4 w-4" strokeWidth={1.6} />
            {cta}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function EmptyStatesPreview({ theme }: { theme: ThemeMode }) {
  return (
    <ScreenFrame title="Empty states (new tenant)" theme={theme}>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Empty states</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            What a brand-new tenant sees before any data lands. Every directory has a calm, on-brand empty state.
          </p>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <EmptyCard icon={Users} title="No people yet" body="Start building your network by adding your first person. They'll appear here once created." cta="Add first person" />
          <EmptyCard icon={Building2} title="No organizations yet" body="Start building your directory by adding your first organization." cta="Add first organization" />
          <EmptyCard icon={TagsIcon} title="No tags yet" body="Tags appear here once people are tagged in the system." />
          <EmptyCard icon={Building2} title="No tenants yet" body="Tenants segment your contact data into isolated workspaces. Create your first to get started." cta="Create tenant" />
        </div>
      </div>
    </ScreenFrame>
  );
}

// ----------------------------------------------------------------------------
// Combined gallery — every screen, one theme
// ----------------------------------------------------------------------------

export function ScreensPreview({ theme }: { theme: ThemeMode }) {
  return (
    <div className="space-y-8">
      <Labeled label="People — directory (list)"><PeoplePreview theme={theme} layout="list" /></Labeled>
      <Labeled label="People — directory (grid)"><PeoplePreview theme={theme} layout="grid" /></Labeled>
      <Labeled label="Organizations — directory (grid)"><OrgsPreview theme={theme} layout="grid" /></Labeled>
      <Labeled label="Tags — management"><TagsPreview theme={theme} /></Labeled>
      <Labeled label="Tenants — admin"><TenantsPreview theme={theme} /></Labeled>
      <Labeled label="Settings"><SettingsPreview theme={theme} /></Labeled>
      <Labeled label="Review Queue — triage cockpit"><ReviewQueuePreview theme={theme} /></Labeled>
      <Labeled label="Graph — ego network"><GraphPreview theme={theme} /></Labeled>
      <Labeled label="Empty states"><EmptyStatesPreview theme={theme} /></Labeled>
    </div>
  );
}

function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-muted-foreground">{label}</h3>
      {children}
    </div>
  );
}

export default ScreensPreview;
