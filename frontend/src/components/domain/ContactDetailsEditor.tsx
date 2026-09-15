import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useMcpMutation } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import type { ImHandle, PersonIdentifier, PersonUrl } from "@/lib/types";

type PersonId = string;

function SectionCard({
  title,
  description,
  children
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">{children}</CardContent>
    </Card>
  );
}

function EmptyRow() {
  return <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>;
}

function ImHandlesSection({ personId, items }: { personId: PersonId; items?: ImHandle[] }) {
  const add = useMcpMutation(tools.addImHandle);
  const remove = useMcpMutation(tools.removeImHandle);
  const [protocol, setProtocol] = useState("");
  const [handle, setHandle] = useState("");
  const [label, setLabel] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!protocol.trim() || !handle.trim()) return;
    await add.mutateAsync({
      person_id: personId,
      protocol: protocol.trim(),
      handle: handle.trim(),
      label: label.trim() || undefined
    });
    setProtocol("");
    setHandle("");
    setLabel("");
  }

  return (
    <SectionCard title="IM & social handles" description="Instagram, X, Discord, Telegram, …">
      {items?.length ? (
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.id ?? item.handle}
              className="flex items-center justify-between gap-2 rounded-md border border-border bg-background/40 p-2.5"
            >
              <div className="min-w-0">
                <p className="truncate font-medium">
                  {item.handle}
                  {item.label ? <span className="text-muted-foreground"> · {item.label}</span> : null}
                </p>
                <div className="mt-1 flex items-center gap-1.5">
                  <Badge variant="secondary" className="capitalize">
                    {item.protocol ?? "other"}
                  </Badge>
                  {item.is_primary ? (
                    <Badge variant="outline" className="border-success/40 text-success">
                      primary
                    </Badge>
                  ) : null}
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                title="Remove handle"
                aria-label="Remove handle"
                disabled={remove.isPending}
                onClick={() => void remove.mutateAsync({ im_handle_id: item.id })}
              >
                <Trash2 className="h-4 w-4" strokeWidth={1.6} />
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyRow />
      )}
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        <Input
          className="w-28"
          placeholder="instagram"
          value={protocol}
          onChange={(e) => setProtocol(e.target.value)}
          aria-label="Protocol"
        />
        <Input
          className="min-w-40 flex-1"
          placeholder="@handle"
          value={handle}
          onChange={(e) => setHandle(e.target.value)}
          aria-label="Handle"
        />
        <Input
          className="w-28"
          placeholder="label"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          aria-label="Label"
        />
        <Button type="submit" size="sm" disabled={add.isPending}>
          <Plus className="h-4 w-4" strokeWidth={1.6} />
          Add
        </Button>
      </form>
    </SectionCard>
  );
}

function IdentifiersSection({ personId, items }: { personId: PersonId; items?: PersonIdentifier[] }) {
  const add = useMcpMutation(tools.addIdentifier);
  const remove = useMcpMutation(tools.removeIdentifier);
  const [namespace, setNamespace] = useState("");
  const [value, setValue] = useState("");
  const [url, setUrl] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!namespace.trim() || !value.trim()) return;
    await add.mutateAsync({
      subject_kind: "person",
      subject_id: personId,
      namespace: namespace.trim(),
      value: value.trim(),
      url: url.trim() || undefined
    });
    setNamespace("");
    setValue("");
    setUrl("");
  }

  return (
    <SectionCard title="Identifiers" description="Handles, usernames and other namespaced ids.">
      {items?.length ? (
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.id ?? item.value}
              className="flex items-center justify-between gap-2 rounded-md border border-border bg-background/40 p-2.5"
            >
              <div className="min-w-0">
                <p className="truncate font-medium">{item.value}</p>
                <div className="mt-1 flex items-center gap-1.5">
                  <Badge variant="secondary" className="capitalize">
                    {item.namespace ?? "other"}
                  </Badge>
                  {item.verified ? (
                    <Badge variant="outline" className="border-success/40 text-success">
                      verified
                    </Badge>
                  ) : null}
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                title="Remove identifier"
                aria-label="Remove identifier"
                disabled={remove.isPending}
                onClick={() => void remove.mutateAsync({ identifier_id: item.id })}
              >
                <Trash2 className="h-4 w-4" strokeWidth={1.6} />
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyRow />
      )}
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        <Input
          className="w-32"
          placeholder="namespace"
          value={namespace}
          onChange={(e) => setNamespace(e.target.value)}
          aria-label="Namespace"
        />
        <Input
          className="min-w-40 flex-1"
          placeholder="value"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label="Value"
        />
        <Input
          className="w-40"
          placeholder="url (optional)"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          aria-label="URL"
        />
        <Button type="submit" size="sm" disabled={add.isPending}>
          <Plus className="h-4 w-4" strokeWidth={1.6} />
          Add
        </Button>
      </form>
    </SectionCard>
  );
}

function UrlsSection({ personId, items }: { personId: PersonId; items?: PersonUrl[] }) {
  const add = useMcpMutation(tools.addUrl);
  const remove = useMcpMutation(tools.removeUrl);
  const [url, setUrl] = useState("");
  const [type, setType] = useState("profile");
  const [label, setLabel] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!url.trim()) return;
    await add.mutateAsync({
      person_id: personId,
      url: url.trim(),
      type: type.trim() || "profile",
      label: label.trim() || undefined
    });
    setUrl("");
    setLabel("");
  }

  return (
    <SectionCard title="URLs" description="Website, portfolio, LinkedIn, …">
      {items?.length ? (
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.id ?? item.url}
              className="flex items-center justify-between gap-2 rounded-md border border-border bg-background/40 p-2.5"
            >
              <div className="min-w-0">
                <p className="truncate font-medium">{item.url}</p>
                <div className="mt-1 flex items-center gap-1.5">
                  <Badge variant="secondary" className="capitalize">
                    {item.type ?? "profile"}
                  </Badge>
                  {item.label ? <span className="text-xs text-muted-foreground">{item.label}</span> : null}
                  {item.is_primary ? (
                    <Badge variant="outline" className="border-success/40 text-success">
                      primary
                    </Badge>
                  ) : null}
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                title="Remove URL"
                aria-label="Remove URL"
                disabled={remove.isPending}
                onClick={() => void remove.mutateAsync({ url_id: item.id })}
              >
                <Trash2 className="h-4 w-4" strokeWidth={1.6} />
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyRow />
      )}
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        <Input
          className="min-w-48 flex-1"
          placeholder="https://…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          aria-label="URL"
        />
        <Input
          className="w-28"
          placeholder="profile"
          value={type}
          onChange={(e) => setType(e.target.value)}
          aria-label="Type"
        />
        <Input
          className="w-28"
          placeholder="label"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          aria-label="Label"
        />
        <Button type="submit" size="sm" disabled={add.isPending}>
          <Plus className="h-4 w-4" strokeWidth={1.6} />
          Add
        </Button>
      </form>
    </SectionCard>
  );
}

export function ContactDetailsEditor({
  personId,
  data
}: {
  personId: string;
  data: { im_handles?: ImHandle[]; identifiers?: PersonIdentifier[]; urls?: PersonUrl[] };
}) {
  return (
    <div className="space-y-4">
      <ImHandlesSection personId={personId} items={data.im_handles} />
      <IdentifiersSection personId={personId} items={data.identifiers} />
      <UrlsSection personId={personId} items={data.urls} />
    </div>
  );
}
