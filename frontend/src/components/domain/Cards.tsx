import { Link } from "@tanstack/react-router";
import { Building2, Mail, UserRound } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { initials } from "@/lib/utils";
import type { OrganizationSummary, PersonSummary } from "@/lib/types";
import { TagChip } from "@/components/domain/Badges";

export function PersonCard({ person }: { person: PersonSummary }) {
  return (
    <Link to="/people/$id" params={{ id: person.person_id }} className="block">
      <Card className="transition-colors hover:border-primary/60">
        <CardContent className="flex items-center gap-4 p-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-semibold">
            {initials(person.display_name)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="truncate font-medium">{person.display_name}</p>
              {person.relationship_temperature ? <Badge>{person.relationship_temperature}</Badge> : null}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <Mail className="h-3.5 w-3.5" />
                {person.primary_email ?? "No email"}
              </span>
              <span className="inline-flex items-center gap-1">
                <Building2 className="h-3.5 w-3.5" />
                {person.current_org ?? "No organization"}
              </span>
            </div>
            {person.tags?.length ? (
              <div className="mt-2 flex flex-wrap gap-1">{person.tags.slice(0, 4).map((tag) => <TagChip key={tag} label={tag} />)}</div>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

export function OrgCard({ org }: { org: OrganizationSummary }) {
  return (
    <Link to="/orgs/$id" params={{ id: org.org_id }} className="block">
      <Card className="transition-colors hover:border-primary/60">
        <CardContent className="flex items-center gap-4 p-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-muted">
            <UserRound className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <p className="truncate font-medium">{org.display_name ?? org.legal_name}</p>
            <p className="text-sm text-muted-foreground">{[org.kind, org.domain, org.industry].filter(Boolean).join(" · ")}</p>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
