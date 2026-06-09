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

// The bare design-token CHANNEL (the `--var` name) behind each edge colour, so
// the graph can rebuild the colour with a confidence alpha — `oklch(var(--X)/a)`
// — and the WebGL rasterizer can resolve it. Keep in lockstep with edgeColors.
export const edgeChannels: Record<string, string> = {
  WORKS_AT: "info",
  KNOWS: "success",
  FAMILY_OF: "confidence-rose",
  REPORTS_TO: "warning",
  COUNSEL_FOR: "destructive",
  WITNESS_FOR: "destructive",
  PARTY_TO: "confidence-sky",
  MENTIONED_IN: "muted-foreground",
  DUPLICATE_OF: "confidence-amber",
  TAGGED: "confidence-rose",
  HAS_EMAIL: "confidence-sky",
  HAS_PHONE: "confidence-emerald"
};

// Human-readable label for a relationship type, shown on the association filter
// chips. Falls back to a Title-cased version of the raw type.
export const edgeTypeLabels: Record<string, string> = {
  WORKS_AT: "Works at",
  KNOWS: "Knows",
  FAMILY_OF: "Family",
  REPORTS_TO: "Reports to",
  COUNSEL_FOR: "Counsel for",
  WITNESS_FOR: "Witness for",
  PARTY_TO: "Party to",
  MENTIONED_IN: "Mentioned in",
  DUPLICATE_OF: "Duplicate of",
  TAGGED: "Tagged",
  HAS_EMAIL: "Email",
  HAS_PHONE: "Phone"
};

export function colorForNode(kind: string) {
  return graphColors[kind] ?? graphColors.Entity;
}

export function colorForEdge(kind: string) {
  return edgeColors[kind] ?? "oklch(var(--muted-foreground))";
}

export function edgeChannel(kind: string) {
  return edgeChannels[kind] ?? "muted-foreground";
}

export function labelForEdge(kind: string) {
  return (
    edgeTypeLabels[kind] ??
    kind
      .replace(/_/g, " ")
      .toLowerCase()
      .replace(/^\w/, (c) => c.toUpperCase())
  );
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
