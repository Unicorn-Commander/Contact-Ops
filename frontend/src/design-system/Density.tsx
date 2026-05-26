import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Rows2, Rows3, Rows4 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DensityMode } from "@/design-system/tokens";

interface DensityContextValue {
  density: DensityMode;
  setDensity: (density: DensityMode) => void;
}

const DensityContext = createContext<DensityContextValue | null>(null);
const storageKey = "co-density";

const densityOptions: Array<{
  value: DensityMode;
  label: string;
  icon: typeof Rows2;
}> = [
  { value: "compact", label: "Compact", icon: Rows4 },
  { value: "default", label: "Default", icon: Rows3 },
  { value: "comfortable", label: "Comfortable", icon: Rows2 }
];

function readDensity(defaultDensity: DensityMode) {
  if (typeof window === "undefined") return defaultDensity;
  const stored = window.localStorage.getItem(storageKey);
  return stored === "compact" || stored === "default" || stored === "comfortable" ? stored : defaultDensity;
}

export function defaultDensityForSurface(surface: "inbox" | "crud" | "settings" | "graph"): DensityMode {
  if (surface === "inbox" || surface === "graph") return "compact";
  if (surface === "settings") return "comfortable";
  return "default";
}

export function DensityProvider({
  children,
  defaultDensity = "default"
}: {
  children: ReactNode;
  defaultDensity?: DensityMode;
}) {
  const [density, setDensityState] = useState<DensityMode>(() => readDensity(defaultDensity));

  useEffect(() => {
    document.documentElement.dataset.density = density;
    window.localStorage.setItem(storageKey, density);
  }, [density]);

  const value = useMemo(
    () => ({
      density,
      setDensity: setDensityState
    }),
    [density]
  );

  return <DensityContext.Provider value={value}>{children}</DensityContext.Provider>;
}

export function useDensity() {
  const context = useContext(DensityContext);
  if (context) return context;
  return {
    density: "default" as DensityMode,
    setDensity: () => undefined
  };
}

export function DensityControl({ className }: { className?: string }) {
  const { density, setDensity } = useDensity();

  return (
    <div className={cn("inline-flex rounded-md border border-border bg-muted p-1", className)} role="group" aria-label="Density">
      {densityOptions.map((option) => {
        const Icon = option.icon;
        const active = density === option.value;
        return (
          <button
            key={option.value}
            type="button"
            className={cn(
              "focus-ring inline-flex h-7 items-center gap-1.5 rounded-[var(--radius-sm)] px-2 text-xs font-medium text-muted-foreground transition-colors",
              active && "bg-background text-foreground shadow-[var(--shadow-1)]"
            )}
            aria-pressed={active}
            title={`${option.label} density`}
            onClick={() => setDensity(option.value)}
          >
            <Icon className="h-3.5 w-3.5" strokeWidth={1.6} />
            <span className="hidden sm:inline">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
