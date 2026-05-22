import { History, Pencil, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfidenceBadge } from "@/components/domain/Badges";
import { compactDate } from "@/lib/utils";
import type { ActionEvent, ContactMethod } from "@/lib/types";

export function ContactMethodList({ title, items }: { title: string; items?: ContactMethod[] | null }) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>{title}</CardTitle>
        <Button variant="ghost" size="icon" title={`Edit ${title}`}>
          <Pencil className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {items?.length ? (
          items.map((item, index) => (
            <div key={item.id ?? index} className="flex items-center justify-between rounded-md border p-3">
              <div>
                <p className="font-medium">{item.address ?? item.e164 ?? item.value ?? "Unknown"}</p>
                <div className="mt-1 flex gap-2">
                  <Badge>{item.type ?? item.label ?? "other"}</Badge>
                  {item.is_primary ? <Badge className="text-emerald-700">primary</Badge> : null}
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
      <CardContent className="space-y-3">
        {items.length ? (
          items.map((item, index) => (
            <div key={index} className="border-l-2 border-primary pl-4">
              <p className="font-medium">{String(item.title ?? item.role ?? "Role")}</p>
              <p className="text-sm text-muted-foreground">{String(item.organization_name ?? item.org_name ?? "Organization")}</p>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">No employment history has been attached.</p>
        )}
      </CardContent>
    </Card>
  );
}

export function ActionEventRow({ event }: { event: ActionEvent }) {
  return (
    <div className="flex gap-3 border-b py-3 last:border-b-0">
      <div className="mt-1 flex h-8 w-8 items-center justify-center rounded-full bg-muted">
        {event.status === "applied" ? <ShieldCheck className="h-4 w-4" /> : <History className="h-4 w-4" />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium">{event.event_type ?? event.action_type ?? "action_event"}</p>
          {event.status ? <Badge>{event.status}</Badge> : null}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{compactDate(event.created_at)}</p>
        {event.diff || event.payload ? (
          <pre className="mt-2 max-h-28 overflow-auto rounded-md bg-muted p-2 text-xs">
            {JSON.stringify(event.diff ?? event.payload, null, 2)}
          </pre>
        ) : null}
      </div>
    </div>
  );
}
