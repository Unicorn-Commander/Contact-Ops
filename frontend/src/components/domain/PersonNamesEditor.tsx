import { Check, Pencil, Plus, Star, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useMcpMutation, usePersonNames } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";
import type { NameType, PersonName } from "@/lib/types";

const NAME_TYPES: NameType[] = [
  "legal",
  "preferred",
  "nickname",
  "aka",
  "former",
  "maiden",
  "stage",
];

/**
 * Editable typed-names section for a person (backend mcp/tools/person_names.py).
 * Add / inline-edit / remove / set-primary, mirroring the contact-details editor
 * add/remove pattern. Names are fetched via `list_person_names` and mutated via
 * the `add_person_name` / `update_person_name` / `remove_person_name` /
 * `set_primary_person_name` MCP tools.
 */
export function PersonNamesEditor({ personId }: { personId: string }) {
  const names = usePersonNames(personId);
  const add = useMcpMutation(tools.addPersonName);
  const update = useMcpMutation(tools.updatePersonName);
  const remove = useMcpMutation(tools.removePersonName);
  const setPrimary = useMcpMutation(tools.setPrimaryPersonName);

  const [nameType, setNameType] = useState<NameType>("aka");
  const [value, setValue] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const items = names.data?.items ?? [];

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    await add.mutateAsync({ person_id: personId, name_type: nameType, value: value.trim() });
    setValue("");
  }

  function startEdit(item: PersonName) {
    setEditingId(item.person_name_id);
    setDraft(item.value);
  }

  async function saveEdit(item: PersonName) {
    if (draft.trim() && draft.trim() !== item.value) {
      await update.mutateAsync({ person_name_id: item.person_name_id, value: draft.trim() });
    }
    setEditingId(null);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Names</CardTitle>
        <CardDescription>
          Legal, preferred, nickname, alias, former, maiden, and stage names.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {items.length ? (
          <ul className="space-y-2">
            {items.map((item) => {
              const editing = editingId === item.person_name_id;
              if (editing) {
                return (
                  <li
                    key={item.person_name_id}
                    className="flex flex-wrap items-end gap-2 rounded-md border border-border bg-background/40 p-2.5"
                  >
                    <Input
                      className="min-w-40 flex-1"
                      placeholder="Name"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      aria-label={`Edit ${item.value}`}
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
                <li
                  key={item.person_name_id}
                  className="flex items-center justify-between gap-2 rounded-md border border-border bg-background/40 p-2.5"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium">{item.value}</p>
                    <div className="mt-1 flex items-center gap-1.5">
                      <Badge variant="secondary" className="capitalize">
                        {item.name_type}
                      </Badge>
                      {item.is_primary ? (
                        <Badge variant="outline" className="border-success/40 text-success">
                          primary
                        </Badge>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-0.5">
                    {!item.is_primary ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        title="Make primary"
                        aria-label={`Make ${item.value} primary`}
                        disabled={setPrimary.isPending}
                        onClick={() =>
                          void setPrimary.mutateAsync({ person_name_id: item.person_name_id })
                        }
                      >
                        <Star className="h-4 w-4" strokeWidth={1.6} />
                      </Button>
                    ) : null}
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Edit name"
                      aria-label={`Edit ${item.value}`}
                      onClick={() => startEdit(item)}
                    >
                      <Pencil className="h-4 w-4" strokeWidth={1.6} />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Remove name"
                      aria-label={`Remove ${item.value}`}
                      disabled={remove.isPending}
                      onClick={() =>
                        void remove.mutateAsync({ person_name_id: item.person_name_id })
                      }
                    >
                      <Trash2 className="h-4 w-4" strokeWidth={1.6} />
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No names recorded yet.</p>
        )}

        <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
          <select
            aria-label="Name type"
            value={nameType}
            onChange={(e) => setNameType(e.target.value as NameType)}
            className="focus-ring h-9 rounded-md border border-border bg-background px-2 text-sm capitalize text-foreground"
          >
            {NAME_TYPES.map((t) => (
              <option key={t} value={t} className="capitalize">
                {t}
              </option>
            ))}
          </select>
          <Input
            className="min-w-40 flex-1"
            placeholder="Name"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-label="Name"
          />
          <Button type="submit" size="sm" disabled={add.isPending}>
            <Plus className="h-4 w-4" strokeWidth={1.6} />
            Add
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
