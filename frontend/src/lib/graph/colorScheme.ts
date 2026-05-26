export const graphColors: Record<string, string> = {
  Person: "oklch(var(--primary))",
  Organization: "oklch(var(--info))",
  Topic: "oklch(var(--warning))",
  Tag: "oklch(var(--confidence-rose))",
  Email: "oklch(var(--confidence-sky))",
  Phone: "oklch(var(--confidence-emerald))",
  Agent: "oklch(var(--confidence-amber))",
  Entity: "oklch(var(--muted-foreground))"
};

export const edgeColors: Record<string, string> = {
  WORKS_AT: "oklch(var(--info))",
  KNOWS: "oklch(var(--success))",
  FAMILY_OF: "oklch(var(--confidence-rose))",
  REPORTS_TO: "oklch(var(--warning))",
  COUNSEL_FOR: "oklch(var(--destructive))",
  WITNESS_FOR: "oklch(var(--destructive))",
  PARTY_TO: "oklch(var(--confidence-sky))",
  MENTIONED_IN: "oklch(var(--muted-foreground))",
  DUPLICATE_OF: "oklch(var(--confidence-amber))",
  TAGGED: "oklch(var(--confidence-rose))",
  HAS_EMAIL: "oklch(var(--confidence-sky))",
  HAS_PHONE: "oklch(var(--confidence-emerald))"
};

export function colorForNode(kind: string) {
  return graphColors[kind] ?? graphColors.Entity;
}

export function colorForEdge(kind: string) {
  return edgeColors[kind] ?? "oklch(var(--muted-foreground))";
}

const proposeOnlyEdgeKinds = new Set([
  "COUNSEL_FOR",
  "WITNESS_FOR",
  "FAMILY_OF",
  "PARTY_TO",
  "DUPLICATE_OF"
]);

export function isProposeOnlyRelationship(kind: string) {
  return proposeOnlyEdgeKinds.has(kind);
}
