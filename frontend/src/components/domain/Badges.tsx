import { Badge } from "@/components/ui/badge";

export function TagChip({ label }: { label: string }) {
  return <Badge className="border-indigo-200 bg-indigo-50 text-indigo-700 dark:border-indigo-900 dark:bg-indigo-950 dark:text-indigo-200">{label}</Badge>;
}

export function ConfidenceBadge({
  confidence,
  source
}: {
  confidence?: number | null;
  source?: string | null;
}) {
  const score = typeof confidence === "number" ? `${Math.round(confidence * 100)}%` : "Human";
  return (
    <Badge title={source ? `Source: ${source}` : "Field provenance"}>
      {score}
      {source ? ` by ${source}` : ""}
    </Badge>
  );
}
