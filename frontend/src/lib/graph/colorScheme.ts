export const graphColors: Record<string, string> = {
  Person: "#22c55e",
  Organization: "#38bdf8",
  Topic: "#f59e0b",
  Tag: "#a78bfa",
  Email: "#f472b6",
  Phone: "#14b8a6",
  Entity: "#94a3b8"
};

export const edgeColors: Record<string, string> = {
  WORKS_AT: "#38bdf8",
  KNOWS: "#22c55e",
  FAMILY_OF: "#f472b6",
  REPORTS_TO: "#f59e0b",
  COUNSEL_FOR: "#ef4444",
  WITNESS_FOR: "#fb7185",
  PARTY_TO: "#a78bfa",
  MENTIONED_IN: "#94a3b8",
  DUPLICATE_OF: "#f97316",
  TAGGED: "#a78bfa",
  HAS_EMAIL: "#f472b6",
  HAS_PHONE: "#14b8a6"
};

export function colorForNode(kind: string) {
  return graphColors[kind] ?? graphColors.Entity;
}

export function colorForEdge(kind: string) {
  return edgeColors[kind] ?? "#64748b";
}
