import { Check, Pencil, Plus, Star, Trash2, X } from "lucide-react";
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

/** Row chrome shared by every section: the value block plus its action buttons. */
function Row({
  children,
  actions
}: {
  children: React.ReactNode;
  actions: React.ReactNode;
}) {
  return (
    <li className="flex items-center justify-between gap-2 rounded-md border border-border bg-background/40 p-2.5">
      <div className="min-w-0">{children}</div>
      <div className="flex shrink-0 items-center gap-0.5">{actions}</div>
    </li>
  );
}

function ImHandlesSection({ personId, items }: { personId: PersonId; items?: ImHandle[] }) {
  const add = useMcpMutation(tools.addImHandle);
  const update = useMcpMutation(tools.updateImHandle);
  const remove = useMcpMutation(tools.removeImHandle);
  const [protocol, setProtocol] = useState("");
  const [handle, setHandle] = useState("");
  const [label, setLabel] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState({ protocol: "", handle: "", label: "" });

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

  function startEdit(item: ImHandle) {
    if (!item.id) return;
    setEditingId(item.id);
    setDraft({
      protocol: item.protocol ?? "",
      handle: item.handle ?? "",
      label: item.label ?? ""
    });
  }

  async function saveEdit(item: ImHandle) {
    if (!item.id) return;
    const patch: Record<string, unknown> = { im_handle_id: item.id };
    if (draft.protocol.trim() && draft.protocol.trim() !== (item.protocol ?? "")) {
      patch.protocol = draft.protocol.trim();
    }
    if (draft.handle.trim() && draft.handle.trim() !== (item.handle ?? "")) {
      patch.handle = draft.handle.trim();
    }
    if (draft.label.trim() !== (item.label ?? "")) {
      patch.label = draft.label.trim();
    }
    if (Object.keys(patch).length > 1) await update.mutateAsync(patch);
    setEditingId(null);
  }

  return (
    <SectionCard title="IM & social handles" description="Instagram, X, Discord, Telegram, …">
      {items?.length ? (
        <ul className="space-y-2">
          {items.map((item) => {
            const editing = editingId != null && editingId === item.id;
            if (editing) {
              return (
                <li
                  key={item.id ?? item.handle}
                  className="flex flex-wrap items-end gap-2 rounded-md border border-border bg-background/40 p-2.5"
                >
                  <Input
                    className="w-28"
                    placeholder="instagram"
                    value={draft.protocol}
                    onChange={(e) => setDraft((d) => ({ ...d, protocol: e.target.value }))}
                    aria-label="Edit protocol"
                  />
                  <Input
                    className="min-w-40 flex-1"
                    placeholder="@handle"
                    value={draft.handle}
                    onChange={(e) => setDraft((d) => ({ ...d, handle: e.target.value }))}
                    aria-label="Edit handle"
                  />
                  <Input
                    className="w-28"
                    placeholder="label"
                    value={draft.label}
                    onChange={(e) => setDraft((d) => ({ ...d, label: e.target.value }))}
                    aria-label="Edit label"
                  />
                  <Button
                    size="sm"
                    disabled={update.isPending}
                    onClick={() => void saveEdit(item)}
                  >
                    <Check className="h-4 w-4" strokeWidth={1.6} />
                    Save
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setEditingId(null)}
                  >
                    <X className="h-4 w-4" strokeWidth={1.6} />
                    Cancel
                  </Button>
                </li>
              );
            }
            return (
              <Row
                key={item.id ?? item.handle}
                actions={
                  <>
                    {!item.is_primary ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        title="Make primary"
                        aria-label="Make primary handle"
                        disabled={!item.id || update.isPending}
                        onClick={() =>
                          void update.mutateAsync({ im_handle_id: item.id, is_primary: true })
                        }
                      >
                        <Star className="h-4 w-4" strokeWidth={1.6} />
                      </Button>
                    ) : null}
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Edit handle"
                      aria-label="Edit handle"
                      disabled={!item.id}
                      onClick={() => startEdit(item)}
                    >
                      <Pencil className="h-4 w-4" strokeWidth={1.6} />
                    </Button>
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
                  </>
                }
              >
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
              </Row>
            );
          })}
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
  const update = useMcpMutation(tools.updateIdentifier);
  const remove = useMcpMutation(tools.removeIdentifier);
  const [namespace, setNamespace] = useState("");
  const [value, setValue] = useState("");
  const [url, setUrl] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState({ namespace: "", value: "", url: "" });

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

  function startEdit(item: PersonIdentifier) {
    if (!item.id) return;
    setEditingId(item.id);
    setDraft({ namespace: item.namespace ?? "", value: item.value ?? "", url: item.url ?? "" });
  }

  async function saveEdit(item: PersonIdentifier) {
    if (!item.id) return;
    const patch: Record<string, unknown> = { identifier_id: item.id };
    if (draft.namespace.trim() && draft.namespace.trim() !== (item.namespace ?? "")) {
      patch.namespace = draft.namespace.trim();
    }
    if (draft.value.trim() && draft.value.trim() !== (item.value ?? "")) {
      patch.value = draft.value.trim();
    }
    if (draft.url.trim() !== (item.url ?? "")) {
      patch.url = draft.url.trim();
    }
    if (Object.keys(patch).length > 1) await update.mutateAsync(patch);
    setEditingId(null);
  }

  return (
    <SectionCard title="Identifiers" description="Handles, usernames and other namespaced ids.">
      {items?.length ? (
        <ul className="space-y-2">
          {items.map((item) => {
            const editing = editingId != null && editingId === item.id;
            if (editing) {
              return (
                <li
                  key={item.id ?? item.value}
                  className="flex flex-wrap items-end gap-2 rounded-md border border-border bg-background/40 p-2.5"
                >
                  <Input
                    className="w-32"
                    placeholder="namespace"
                    value={draft.namespace}
                    onChange={(e) => setDraft((d) => ({ ...d, namespace: e.target.value }))}
                    aria-label="Edit namespace"
                  />
                  <Input
                    className="min-w-40 flex-1"
                    placeholder="value"
                    value={draft.value}
                    onChange={(e) => setDraft((d) => ({ ...d, value: e.target.value }))}
                    aria-label="Edit value"
                  />
                  <Input
                    className="w-40"
                    placeholder="url (optional)"
                    value={draft.url}
                    onChange={(e) => setDraft((d) => ({ ...d, url: e.target.value }))}
                    aria-label="Edit identifier URL"
                  />
                  <Button size="sm" disabled={update.isPending} onClick={() => void saveEdit(item)}>
                    <Check className="h-4 w-4" strokeWidth={1.6} />
                    Save
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}>
                    <X className="h-4 w-4" strokeWidth={1.6} />
                    Cancel
                  </Button>
                </li>
              );
            }
            return (
              <Row
                key={item.id ?? item.value}
                actions={
                  <>
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Edit identifier"
                      aria-label="Edit identifier"
                      disabled={!item.id}
                      onClick={() => startEdit(item)}
                    >
                      <Pencil className="h-4 w-4" strokeWidth={1.6} />
                    </Button>
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
                  </>
                }
              >
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
              </Row>
            );
          })}
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
  const update = useMcpMutation(tools.updateUrl);
  const remove = useMcpMutation(tools.removeUrl);
  const [url, setUrl] = useState("");
  const [type, setType] = useState("profile");
  const [label, setLabel] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState({ url: "", type: "", label: "" });

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

  function startEdit(item: PersonUrl) {
    if (!item.id) return;
    setEditingId(item.id);
    setDraft({ url: item.url ?? "", type: item.type ?? "", label: item.label ?? "" });
  }

  async function saveEdit(item: PersonUrl) {
    if (!item.id) return;
    const patch: Record<string, unknown> = { url_id: item.id };
    if (draft.url.trim() && draft.url.trim() !== (item.url ?? "")) {
      patch.url = draft.url.trim();
    }
    if (draft.type.trim() && draft.type.trim() !== (item.type ?? "")) {
      patch.type = draft.type.trim();
    }
    if (draft.label.trim() !== (item.label ?? "")) {
      patch.label = draft.label.trim();
    }
    if (Object.keys(patch).length > 1) await update.mutateAsync(patch);
    setEditingId(null);
  }

  return (
    <SectionCard title="URLs" description="Website, portfolio, LinkedIn, …">
      {items?.length ? (
        <ul className="space-y-2">
          {items.map((item) => {
            const editing = editingId != null && editingId === item.id;
            if (editing) {
              return (
                <li
                  key={item.id ?? item.url}
                  className="flex flex-wrap items-end gap-2 rounded-md border border-border bg-background/40 p-2.5"
                >
                  <Input
                    className="min-w-48 flex-1"
                    placeholder="https://…"
                    value={draft.url}
                    onChange={(e) => setDraft((d) => ({ ...d, url: e.target.value }))}
                    aria-label="Edit URL"
                  />
                  <Input
                    className="w-28"
                    placeholder="profile"
                    value={draft.type}
                    onChange={(e) => setDraft((d) => ({ ...d, type: e.target.value }))}
                    aria-label="Edit type"
                  />
                  <Input
                    className="w-28"
                    placeholder="label"
                    value={draft.label}
                    onChange={(e) => setDraft((d) => ({ ...d, label: e.target.value }))}
                    aria-label="Edit URL label"
                  />
                  <Button size="sm" disabled={update.isPending} onClick={() => void saveEdit(item)}>
                    <Check className="h-4 w-4" strokeWidth={1.6} />
                    Save
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}>
                    <X className="h-4 w-4" strokeWidth={1.6} />
                    Cancel
                  </Button>
                </li>
              );
            }
            return (
              <Row
                key={item.id ?? item.url}
                actions={
                  <>
                    {!item.is_primary ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        title="Make primary"
                        aria-label="Make primary URL"
                        disabled={!item.id || update.isPending}
                        onClick={() => void update.mutateAsync({ url_id: item.id, is_primary: true })}
                      >
                        <Star className="h-4 w-4" strokeWidth={1.6} />
                      </Button>
                    ) : null}
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Edit URL"
                      aria-label="Edit URL"
                      disabled={!item.id}
                      onClick={() => startEdit(item)}
                    >
                      <Pencil className="h-4 w-4" strokeWidth={1.6} />
                    </Button>
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
                  </>
                }
              >
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
              </Row>
            );
          })}
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
