/**
 * RelationshipCategoryPicker — set the OWNER's personal relationship to a
 * contact (friend, coworker, family, …). This is the caller's per-tenant view,
 * not a shared fact about the person; it's stored on the tenant membership and
 * surfaced via get_person / search_people as `relationship_category`.
 *
 * Offers the common categories plus free choice isn't needed here — the curated
 * list keeps the directory's categories consistent and filterable. "Clear"
 * removes the label.
 */
import { Check, Users, X } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const RELATIONSHIP_CATEGORIES = [
  "Friend",
  "Family",
  "Coworker",
  "Colleague",
  "Client",
  "Vendor",
  "Acquaintance",
  "Mentor",
  "Mentee",
  "Classmate",
  "Neighbor",
  "Partner",
] as const;

export function RelationshipCategoryPicker({
  value,
  onSelect,
  busy = false,
}: {
  value?: string | null;
  onSelect: (category: string | null) => void;
  busy?: boolean;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          disabled={busy}
          className="gap-1.5 capitalize"
          title="Your relationship to this contact"
        >
          <Users className="h-4 w-4" strokeWidth={1.6} />
          {value || "Relationship"}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        <DropdownMenuLabel className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Your relationship
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {RELATIONSHIP_CATEGORIES.map((category) => {
          const active = value?.toLowerCase() === category.toLowerCase();
          return (
            <DropdownMenuItem
              key={category}
              onSelect={() => onSelect(category)}
              className="flex items-center gap-2 capitalize"
            >
              {active ? (
                <Check className="h-3.5 w-3.5 text-primary" strokeWidth={2} />
              ) : (
                <span className="w-3.5" aria-hidden="true" />
              )}
              {category}
            </DropdownMenuItem>
          );
        })}
        {value ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => onSelect(null)}
              className={cn("flex items-center gap-2 text-muted-foreground")}
            >
              <X className="h-3.5 w-3.5" strokeWidth={1.8} />
              Clear
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
