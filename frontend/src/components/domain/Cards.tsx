import { Link } from "@tanstack/react-router";
import { formatDistanceToNow } from "date-fns";
import { Building2, ChevronRight, Mail } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { TagChip } from "@/components/domain/Badges";
import { cn } from "@/lib/utils";
import { initials } from "@/lib/utils";
import type { OrganizationSummary, PersonSummary } from "@/lib/types";

/**
 * The avatar puck shared by every entity card. People show initials; orgs and
 * other non-human entities show a glyph. People are humans, so a human-shaped
 * avatar is correct here — the AIG "never make an agent look human" rule applies
 * to agents, not to the people in the registry.
 */
function EntityAvatar({ name, icon: Icon }: { name?: string | null; icon?: LucideIcon }) {
  return (
    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary/12 text-sm font-semibold text-primary">
      {Icon ? <Icon className="h-5 w-5" strokeWidth={1.6} /> : initials(name)}
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
              {person.relationship_temperature ? (
                <Badge variant="secondary" className="shrink-0 capitalize">
                  {person.relationship_temperature}
                </Badge>
              ) : null}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
              <span className={metaItem}>
                <Mail className="h-3.5 w-3.5 shrink-0" strokeWidth={1.6} />
                <span className="truncate">{person.primary_email ?? "No email"}</span>
              </span>
              <span className={metaItem}>
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

export function OrgCard({ org }: { org: OrganizationSummary }) {
  const meta = [org.kind, org.domain, org.industry].filter(Boolean).join(" · ");
  return (
    <Link
      to="/orgs/$id"
      params={{ id: org.org_id }}
      aria-label={`Open ${org.display_name ?? org.legal_name}`}
      className="focus-ring group block rounded-lg"
    >
      <Card className={cardChrome()}>
        <CardContent className="flex items-center gap-4 p-4">
          <EntityAvatar icon={Building2} />
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{org.display_name ?? org.legal_name}</p>
            <p className="truncate text-sm text-muted-foreground">{meta || "No details yet"}</p>
          </div>
          <ChevronRight
            className="ml-auto h-4 w-4 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground"
            strokeWidth={1.6}
          />
        </CardContent>
      </Card>
    </Link>
  );
}
