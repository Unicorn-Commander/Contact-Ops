/**
 * ModelLoader — the on-device (WebGPU) model picker + loader.
 * ===========================================================
 * Ported from Magic_Unicorn_Outreach/client-webgpu ModelLoader, re-skinned to
 * Contact-Ops design tokens and wired to `useOndeviceLLM` instead of the
 * outreach app's bespoke `WebGPULLM` class.
 *
 * Responsibilities:
 *   - detect WebGPU support on mount (via `detectWebGPU()` from lib/ondevice/llm)
 *     and show a clear "not supported" card when the browser can't run it
 *   - let the user pick from `LOCAL_MODELS` (defaulting to DEFAULT_LOCAL_MODEL)
 *   - drive the download/compile via `load(modelId)` and render the progress
 *     bar + status text the hook reports
 *   - surface ready / error states, with a retry on error
 *
 * Everything here runs in the browser: the model is fetched from the HF CDN and
 * cached by the browser, then compiled to WebGPU. There are ZERO calls to the
 * Contact-Ops backend from this component.
 */
import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Cpu, Download, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  DEFAULT_LOCAL_MODEL,
  LOCAL_MODELS,
  detectWebGPU,
  findModel,
  type ModelStatus
} from "@/lib/ondevice/llm";

export interface ModelLoaderProps {
  /** Current load status, from `useOndeviceLLM().status`. */
  status: ModelStatus;
  /** Load (or re-load) a model by id, from `useOndeviceLLM().load`. */
  onLoad: (modelId: string) => Promise<unknown>;
  /** Optional: collapse to a compact inline card (used inside the analyst rail). */
  compact?: boolean;
  className?: string;
}

export function ModelLoader({ status, onLoad, compact = false, className }: ModelLoaderProps) {
  const [selectedModel, setSelectedModel] = useState<string>(DEFAULT_LOCAL_MODEL);
  const [webGPUSupported, setWebGPUSupported] = useState<boolean | null>(null);

  // Probe WebGPU once on mount.
  useEffect(() => {
    let cancelled = false;
    void detectWebGPU().then((supported) => {
      if (!cancelled) setWebGPUSupported(supported);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLoad = () => {
    void onLoad(selectedModel).catch((error) => {
      // The hook already folds the failure into `status.error`; this catch just
      // keeps the unhandled-rejection noise out of the console.
      console.error("On-device model load failed:", error);
    });
  };

  const selected = findModel(selectedModel);

  // ---- WebGPU unavailable -------------------------------------------------
  if (webGPUSupported === false) {
    return (
      <div
        className={cn(
          "rounded-xl border border-[oklch(var(--co-rose-500)/0.4)] bg-[oklch(var(--co-rose-500)/0.06)] p-4",
          className
        )}
      >
        <div className="flex items-center gap-2">
          <AlertCircle className="h-5 w-5 shrink-0 text-[oklch(var(--co-rose-500))]" strokeWidth={1.8} />
          <h3 className="text-sm font-semibold text-[oklch(var(--co-rose-500))]">WebGPU not available</h3>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          On-device AI needs WebGPU, which this browser doesn't expose. Try a recent Chrome or Edge (113+),
          or Safari 18+, with hardware acceleration enabled.
        </p>
      </div>
    );
  }

  const isLoading = status.status === "loading";
  const isReady = status.status === "ready";
  const isError = status.status === "error";

  return (
    <div className={cn("rounded-xl border border-border bg-card p-4 shadow-[var(--shadow-1)]", className)}>
      <div className="flex items-center gap-2">
        <span
          className="bg-gradient-brand flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-primary-foreground shadow-[var(--shadow-1)]"
          aria-hidden="true"
        >
          <Cpu className="h-4 w-4" strokeWidth={1.8} />
        </span>
        <div className="min-w-0 leading-tight">
          <h3 className="text-sm font-semibold">On-device model</h3>
          <p className="text-[11px] text-muted-foreground">
            Runs locally in your browser. Nothing leaves this device.
          </p>
        </div>
        {isReady ? (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-[oklch(var(--co-emerald-500)/0.12)] px-2 py-0.5 text-[11px] font-medium text-[oklch(var(--co-emerald-500))]">
            <CheckCircle2 className="h-3 w-3" strokeWidth={2} />
            Ready
          </span>
        ) : null}
      </div>

      {/* Idle / pre-load: model picker + load button. */}
      {(status.status === "idle" || isError) && (
        <div className="mt-3 space-y-3">
          <div>
            <label
              htmlFor="ondevice-model"
              className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground"
            >
              Model
            </label>
            <select
              id="ondevice-model"
              value={selectedModel}
              onChange={(event) => setSelectedModel(event.target.value)}
              disabled={webGPUSupported === null}
              className="focus-ring w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
            >
              {LOCAL_MODELS.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.label} ({model.size})
                </option>
              ))}
            </select>
            {selected ? (
              <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">{selected.note}</p>
            ) : null}
          </div>

          <Button
            type="button"
            variant="gradient"
            className="w-full"
            onClick={handleLoad}
            disabled={webGPUSupported === null}
          >
            <Download className="h-4 w-4" strokeWidth={1.8} />
            {isError ? "Retry download" : "Load model"}
          </Button>

          {isError ? (
            <p className="flex items-start gap-1.5 text-xs text-[oklch(var(--co-rose-500))]">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.8} />
              <span>{status.error}</span>
            </p>
          ) : (
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              First load downloads the model (cached by your browser afterward) and compiles it to WebGPU.
              {compact ? "" : " Larger models give better answers but take longer to load."}
            </p>
          )}
        </div>
      )}

      {/* Loading: progress bar + phase text. */}
      {isLoading && (
        <div className="mt-3 space-y-2">
          <div className="flex items-center gap-2 text-sm text-foreground">
            <Loader2 className="h-4 w-4 animate-spin text-[oklch(var(--co-brand-500))]" strokeWidth={1.8} />
            <span className="truncate">{status.text || "Loading…"}</span>
          </div>
          <div
            className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round((status.progress ?? 0) * 100)}
          >
            <div
              className="bg-gradient-brand h-full rounded-full transition-[width] duration-300"
              style={{ width: `${Math.max(2, Math.round((status.progress ?? 0) * 100))}%` }}
            />
          </div>
          <p className="text-[11px] text-muted-foreground">
            {Math.round((status.progress ?? 0) * 100)}% — keep this tab open while it loads.
          </p>
        </div>
      )}

      {/* Ready: quiet confirmation (the badge in the header already signals it). */}
      {isReady ? (
        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          {findModel(status.modelId)?.label ?? status.modelId} is loaded and answering on-device.
        </p>
      ) : null}
    </div>
  );
}
