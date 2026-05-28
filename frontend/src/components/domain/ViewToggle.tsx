import { LayoutGrid, List } from "lucide-react";
import { cn } from "@/lib/utils";

export type DirectoryLayout = "list" | "grid";

/**
 * A compact segmented control for switching a directory between list and grid
 * layouts. Styled to match the shadcn Tabs primitive (muted track, active
 * segment lifts to the background surface) so it reads as a native part of the
 * command center. Purely presentational — the parent owns the layout state.
 */
export function ViewToggle({
  value,
  onChange,
  className
}: {
  value: DirectoryLayout;
  onChange: (next: DirectoryLayout) => void;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label="Directory layout"
      className={cn("inline-flex h-9 items-center gap-0.5 rounded-md bg-muted p-1", className)}
    >
      <Segment active={value === "list"} label="List view" onClick={() => onChange("list")}>
        <List className="h-4 w-4" strokeWidth={1.8} />
      </Segment>
      <Segment active={value === "grid"} label="Grid view" onClick={() => onChange("grid")}>
        <LayoutGrid className="h-4 w-4" strokeWidth={1.8} />
      </Segment>
    </div>
  );
}

function Segment({
  active,
  label,
  onClick,
  children
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      aria-label={label}
      title={label}
      className={cn(
        "focus-ring inline-flex h-7 w-7 items-center justify-center rounded transition-colors",
        active ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
      )}
    >
      {children}
    </button>
  );
}
