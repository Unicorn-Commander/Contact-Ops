export function GraphEmptyState({ rootPersonId }: { rootPersonId?: string }) {
  return <div className="space-y-2">
    <h2 className="text-base font-semibold text-foreground">No projected relationships yet</h2>
    <p className="text-sm text-muted-foreground">
      {rootPersonId ? "This person's graph projection is empty." : "This workspace's graph projection is empty."}
      {" "}Stored contacts and affiliations may still be available in their directories.
    </p>
  </div>;
}
