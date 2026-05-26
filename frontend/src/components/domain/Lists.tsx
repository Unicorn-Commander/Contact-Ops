import { Pencil } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfidenceBadge } from "@/components/domain/Badges";
import { ActionAttribution, type AttributionActor } from "@/design-system";
import type { ActionEvent, ContactMethod } from "@/lib/types";

export function ContactMethodList({ title, items }: { title: string; items?: ContactMethod[] | null }) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>{title}</CardTitle>
        <Button variant="ghost" size="icon" title={`Edit ${title}`} aria-label={`Edit ${title}`}>
          <Pencil className="h-4 w-4" strokeWidth={1.6} />
        </Button>
      </CardHeader>
      <CardContent className="space-y-2">
        {items?.length ? (
          items.map((item, index) => (
            <div
              key={item.id ?? index}
              className="flex flex-col gap-2 rounded-md border border-border bg-background/40 p-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="truncate font-medium">{item.address ?? item.e164 ?? item.value ?? "Unknown"}</p>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <Badge variant="secondary" className="capitalize">
                    {item.type ?? item.label ?? "other"}
                  </Badge>
                  {item.is_primary ? (
                    <Badge variant="outline" className="border-success/40 text-success">
                      primary
                    </Badge>
                  ) : null}
                </div>
              </div>
              <ConfidenceBadge confidence={item.confidence} source={item.source_label} />
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>
        )}
      </CardContent>
    </Card>
  );
}

export function EmploymentTimeline({ items = [] }: { items?: Array<Record<string, unknown>> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Employment timeline</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {items.length ? (
          items.map((item, index) => (
            <div key={index} className="relative border-l-2 border-primary/60 pl-4">
              <span aria-hidden className="absolute -left-[5px] top-1.5 h-2 w-2 rounded-full bg-primary" />
              <p className="font-medium">{String(item.title ?? item.role ?? "Role")}</p>
              <p className="text-sm text-muted-foreground">
                {String(item.organization_name ?? item.org_name ?? "Organization")}
              </p>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">No employment history has been attached.</p>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Resolve an audit event's actor into an AIG attribution actor. A confidence
 * score or an agent/service/bridge marker means the actor is an agent; a named
 * person with no agent markers is a human. Errs toward `agent` only when there
 * is no human name, so human authorship is never silently relabelled.
 */
function deriveActor(event: ActionEvent): AttributionActor {
  const actor = (event.actor ?? {}) as Record<string, unknown>;
  const declaredType = typeof actor.type === "string" ? actor.type.toLowerCase() : undefined;
  const slug = (actor.slug ?? actor.agent_id ?? actor.agent_slug ?? actor.id) as string | undefined;
  const name = (actor.name ?? actor.display_name ?? actor.full_name) as string | undefined;
  const looksAutomated = /agent|bot|system|import|sync|recon|bridge|service/i.test(
    String(slug ?? name ?? "")
  );

  const isHuman = declaredType === "human" || declaredType === "user" || (!!name && !looksAutomated && !declaredType);
  if (isHuman) {
    return { type: "human", name: String(name ?? "Unknown"), label: name ?? "Unknown" };
  }
  const agentSlug = String(slug ?? name ?? "system");
  return { type: "agent", slug: agentSlug, label: name ?? slug ?? "system" };
}

export function ActionEventRow({ event }: { event: ActionEvent }) {
  const actor = deriveActor(event);
  const verb = String(event.event_type ?? event.action_type ?? "action").replace(/[_.]/g, " ");
  const payload = event.diff ?? event.payload;

  return (
    <div className="flex flex-col gap-1.5 border-b border-border py-3 last:border-b-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ActionAttribution actor={actor} verb={verb} timestamp={event.created_at ?? Date.now()} />
        {event.status ? (
          <Badge variant="secondary" className="capitalize">
            {event.status}
          </Badge>
        ) : null}
      </div>
      {payload ? (
        <pre className="co-scrollbar mt-1 max-h-28 overflow-auto rounded-md border border-border bg-muted/50 p-2 text-xs">
          {JSON.stringify(payload, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
