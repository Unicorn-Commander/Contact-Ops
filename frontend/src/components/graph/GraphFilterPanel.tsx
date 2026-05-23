import { Button } from "@/components/ui/button";

const edgeKinds = ["WORKS_AT", "KNOWS", "FAMILY_OF", "COUNSEL_FOR", "WITNESS_FOR", "DUPLICATE_OF"];

export function GraphFilterPanel({
  hopLimit,
  confidenceFloor,
  onHopLimitChange,
  onConfidenceFloorChange
}: {
  hopLimit: number;
  confidenceFloor: number;
  onHopLimitChange: (value: number) => void;
  onConfidenceFloorChange: (value: number) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <div className="mb-2 flex items-center justify-between text-xs">
          <span>Hops</span>
          <span>{hopLimit}</span>
        </div>
        <input
          className="w-full accent-emerald-500"
          type="range"
          min={1}
          max={2}
          value={hopLimit}
          onChange={(event) => onHopLimitChange(Number(event.target.value))}
        />
      </div>
      <div>
        <div className="mb-2 flex items-center justify-between text-xs">
          <span>Confidence</span>
          <span>{Math.round(confidenceFloor * 100)}%</span>
        </div>
        <input
          className="w-full accent-emerald-500"
          type="range"
          min={0}
          max={0.95}
          step={0.05}
          value={confidenceFloor}
          onChange={(event) => onConfidenceFloorChange(Number(event.target.value))}
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        {edgeKinds.map((kind) => (
          <Button key={kind} size="sm" variant="outline" className="h-8 justify-start px-2 text-[11px]">
            {kind}
          </Button>
        ))}
      </div>
    </div>
  );
}
