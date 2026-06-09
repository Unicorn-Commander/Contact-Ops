import { Link } from "@tanstack/react-router";
import { formatDistanceToNow } from "date-fns";
import { Briefcase, Building2, ChevronRight, Globe, Mail } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { CSSProperties } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { TagChip } from "@/components/domain/Badges";
import { cn } from "@/lib/utils";
import { initials } from "@/lib/utils";
import { avatarColor } from "@/lib/avatar";
import type { OrganizationSummary, PersonSummary } from "@/lib/types";

/**
 * Avatar sizes shared by the list-row and grid-tile entity cards. The grid tile
 * uses a larger puck so the card reads as a directory poster rather than a row.
 */
type AvatarSize = "md" | "lg";

const avatarSize: Record<AvatarSize, string> = {
  md: "h-11 w-11 text-sm",
  lg: "h-12 w-12 text-base"
};

/**
 * Avatar puck style. The deterministic hue logic lives in `@/lib/avatar` so the
 * directory cards and the relationship graph colour the same entity identically.
 */
function avatarStyle(seed?: string | null): CSSProperties {
  return { backgroundColor: avatarColor(seed), color: "white" };
}

/**
 * The avatar puck shared by every entity card. People show initials; orgs and
 * other non-human entities show a glyph. People are humans, so a human-shaped
 * avatar is correct here — the AIG "never make an agent look human" rule applies
 * to agents, not to the people in the registry. The puck colour is hashed from
 * `name` so it is stable and scannable; pass the entity name even for glyph orgs.
 */
function EntityAvatar({ name, icon: Icon, size = "md" }: { name?: string | null; icon?: LucideIcon; size?: AvatarSize }) {
  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center rounded-full font-semibold shadow-[inset_0_1px_0_oklch(1_0_0/0.18),inset_0_0_0_1px_oklch(0_0_0/0.08)]",
        avatarSize[size]
      )}
      style={avatarStyle(name)}
    >
      {Icon ? <Icon className="h-5 w-5" strokeWidth={1.9} /> : initials(name)}
    </div>
  );
}

/** Hover-lift chrome shared by entity cards in curation lists. */
function cardChrome(className?: string) {
  return cn(
    "border-border transition-all duration-150 group-hover:border-primary/30 group-hover:shadow-co2",
    className
  );
}

const metaItem = "inline-flex min-w-0 items-center gap-1.5";

export function PersonCard({ person }: { person: PersonSummary }) {
  const lastInteraction = person.last_interaction_at ? new Date(person.last_interaction_at) : null;

  return (
    <Link
      to="/people/$id"
      params={{ id: person.person_id }}
      aria-label={`Open ${person.display_name}`}
      className="focus-ring group block rounded-lg"
    >
      <Card className={cardChrome()}>
        <CardContent className="flex items-center gap-4 p-4">
          <EntityAvatar name={person.display_name} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="truncate font-medium">{person.display_name}</p>
              {person.relationship_category ? (
                <Badge variant="outline" className="shrink-0 capitalize">
                  {person.relationship_category}
                </Badge>
              ) : null}
              {person.relationship_temperature ? (
                <Badge variant="secondary" className="shrink-0 capitalize">
                  {person.relationship_temperature}
                </Badge>
              ) : null}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
              <span className={cn(metaItem, !person.primary_email && "text-muted-foreground/45")}>
                <Mail className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
                <span className="truncate">{person.primary_email ?? "No email"}</span>
              </span>
              <span className={cn(metaItem, !person.current_org && "text-muted-foreground/45")}>
                <Building2 className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
                <span className="truncate">{person.current_org ?? "No organization"}</span>
              </span>
            </div>
            {person.tags?.length ? (
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {person.tags.slice(0, 3).map((tag) => (
                  <TagChip key={tag} label={tag} />
                ))}
                {person.tags.length > 3 ? (
                  <span className="text-xs text-muted-foreground">+{person.tags.length - 3} more</span>
                ) : null}
              </div>
            ) : null}
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-3 self-start pt-0.5">
            {lastInteraction ? (
              <span
                className="hidden text-xs text-muted-foreground sm:inline co-mono-numeric"
                title={lastInteraction.toLocaleString()}
              >
                {formatDistanceToNow(lastInteraction, { addSuffix: true })}
              </span>
            ) : null}
            <ChevronRight
              className="h-4 w-4 text-muted-foreground transition-colors group-hover:text-foreground"
              strokeWidth={1.6}
            />
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

/**
 * Org-card variant of the shared entity card. Mirrors {@link PersonCard}'s chrome
 * (avatar puck, kind badge, two-line meta, chevron) but carries the org-specific
 * signals — kind, domain, and industry — instead of email/org. Orgs are not human,
 * so the avatar shows the `Building2` glyph rather than initials.
 */
export function OrgCard({ org }: { org: OrganizationSummary }) {
  const name = org.display_name || org.legal_name || "Untitled organization";
  return (
    <Link
      to="/orgs/$id"
      params={{ id: org.org_id }}
      aria-label={`Open ${name}`}
      className="focus-ring group block rounded-lg"
    >
      <Card className={cardChrome()}>
        <CardContent className="flex items-center gap-4 p-4">
          <EntityAvatar name={name} icon={Building2} />
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
              <span className={cn(metaItem, !org.domain && "text-muted-foreground/45")}>
                <Globe className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
                <span className="truncate">{org.domain ?? "No domain"}</span>
              </span>
              <span className={cn(metaItem, !org.industry && "text-muted-foreground/45")}>
                <Briefcase className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
                <span className="truncate">{org.industry ?? "No industry"}</span>
              </span>
            </div>
          </div>
          <ChevronRight
            className="ml-auto h-4 w-4 shrink-0 self-start mt-0.5 text-muted-foreground transition-colors group-hover:text-foreground"
            strokeWidth={1.6}
          />
        </CardContent>
      </Card>
    </Link>
  );
}

/**
 * Grid-tile variant of {@link PersonCard}. Same chrome and signals, laid out as
 * a centered poster for the directory's grid view. The list and grid cards link
 * to the same detail route and carry the same data, so toggling layout never
 * changes meaning — only density.
 */
export function PersonGridCard({ person }: { person: PersonSummary }) {
  const lastInteraction = person.last_interaction_at ? new Date(person.last_interaction_at) : null;
  return (
    <Link
      to="/people/$id"
      params={{ id: person.person_id }}
      aria-label={`Open ${person.display_name}`}
      className="focus-ring group block h-full rounded-lg"
    >
      <Card className={cardChrome("h-full")}>
        <CardContent className="flex h-full flex-col items-center gap-3 p-5 text-center">
          <EntityAvatar name={person.display_name} size="lg" />
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
            <span className={cn(metaItem, "w-full text-sm text-muted-foreground")}>
              <Mail className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
              <span className="truncate">{person.primary_email ?? "No email"}</span>
            </span>
            {lastInteraction ? (
              <span className={cn(metaItem, "co-mono-numeric w-full text-xs text-muted-foreground")}>
                <span className="truncate" title={lastInteraction.toLocaleString()}>
                  Last contact {formatDistanceToNow(lastInteraction, { addSuffix: true })}
                </span>
              </span>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

/** Grid-tile variant of {@link OrgCard}. */
export function OrgGridCard({ org }: { org: OrganizationSummary }) {
  const name = org.display_name || org.legal_name || "Untitled organization";
  return (
    <Link
      to="/orgs/$id"
      params={{ id: org.org_id }}
      aria-label={`Open ${name}`}
      className="focus-ring group block h-full rounded-lg"
    >
      <Card className={cardChrome("h-full")}>
        <CardContent className="flex h-full flex-col items-center gap-3 p-5 text-center">
          <EntityAvatar name={name} icon={Building2} size="lg" />
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
            <span className={cn(metaItem, "w-full text-sm text-muted-foreground")}>
              <Globe className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
              <span className="truncate">{org.domain ?? "No domain"}</span>
            </span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
