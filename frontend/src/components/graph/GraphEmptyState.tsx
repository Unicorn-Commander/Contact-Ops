export function GraphEmptyState({ rootPersonId }: { rootPersonId?: string }) {
  return <div className="space-y-2">
    <h2 className="text-base font-semibold text-foreground">No relationships to display</h2>
    <p className="text-sm text-muted-foreground">
      {rootPersonId ? "No relationships are available for this person yet." : "No relationships are available in this workspace yet."}
      {" "}You can still view contacts and organizations in their directories.
    </p>
  </div>;
}
