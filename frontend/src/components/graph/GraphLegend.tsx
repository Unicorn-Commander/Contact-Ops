import { graphColors, edgeColors } from "@/lib/graph/colorScheme";

export function GraphLegend() {
  return (
    <div className="grid grid-cols-2 gap-x-5 gap-y-2 text-xs">
      {Object.entries(graphColors).map(([label, color]) => (
        <div key={label} className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
          <span>{label}</span>
        </div>
      ))}
      {Object.entries(edgeColors)
        .slice(0, 6)
        .map(([label, color]) => (
          <div key={label} className="flex items-center gap-2">
            <span className="h-0.5 w-4" style={{ backgroundColor: color }} />
            <span>{label}</span>
          </div>
        ))}
    </div>
  );
}
