import type {
  PreTrainedTokenizer,
  TextGenerationPipeline
} from "@huggingface/transformers";

/**
 * Probe of the local device's WebGPU capability + memory headroom.
 *
 * Captured once (cached) before the first model load so the loader can pick a
 * safe starting point and the UI can explain *why* a given model was chosen.
 * `supported` mirrors the legacy `detectWebGPU()` boolean for callers that only
 * need a yes/no. Everything is additive — nothing here changes ModelStatus's
 * existing shape.
 */
export type DeviceProbe = {
  /** True iff a WebGPU adapter was successfully acquired. */
  supported: boolean;
  /** Device the loader will prefer given the probe ("webgpu" | "wasm"). */
  recommendedDevice: "webgpu" | "wasm";
  /** adapter.limits.maxStorageBufferBindingSize in bytes, if known. */
  maxStorageBufferBindingSize?: number;
  /** adapter.limits.maxBufferSize in bytes, if known. */
  maxBufferSize?: number;
  /** Human string for the UI, e.g. "WebGPU · 2.0 GB max buffer". */
  summary: string;
};

/**
 * ModelStatus keeps its original four variants so existing consumers
 * (useOndeviceLLM, ModelLoader) keep compiling unchanged. Each variant gains an
 * OPTIONAL `probe` field — additive only — so the UI can surface device info at
 * any phase without a separate subscription.
 */
export type ModelStatus =
  | { status: "idle"; probe?: DeviceProbe }
  | { status: "loading"; progress: number; text: string; probe?: DeviceProbe }
  | { status: "ready"; modelId: string; probe?: DeviceProbe }
  | { status: "error"; error: string; probe?: DeviceProbe };

/** Capability/positioning tag used by the picker UI. Optional + additive. */
export type ModelTier = "Default" | "Low-memory fallback" | "High-memory / opt-in";

/**
 * Which transformers.js code path loads the model.
 *  - "causal-lm": the generic `pipeline("text-generation", ...)` (Qwen3, etc.)
 *  - "gemma": the multimodal `Gemma4ForConditionalGeneration` + `AutoProcessor`
 *    path, exactly as the onnx-community/gemma-4-E2B-it-ONNX model card shows.
 *    Gemma 4 ONNX is an image-text-to-text model and is NOT loadable through the
 *    text-generation pipeline, so it needs its own loader.
 */
export type ModelFamily = "causal-lm" | "gemma";

export type LocalModel = {
  id: string;
  label: string;
  size: string;
  note: string;
  dtype: "q4f16" | "fp16" | "int8" | "q8";
  /** Code path used to instantiate this model. */
  family: ModelFamily;
  /** Positioning tag for the picker (optional, additive). */
  tier?: ModelTier;
  /**
   * Rough q4f16 runtime working-set in bytes, used by the device probe to
   * decide whether WebGPU has enough headroom. Conservative single-buffer
   * estimate, not the total download size.
   */
  approxBytes?: number;
};

export type LoadProgress = {
  text: string;
  progress?: number;
};

export type ChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};

export interface LocalLLMEngine {
  modelId: string;
  chat(
    messages: ChatMessage[],
    opts: {
      onDelta: (text: string) => void;
      maxTokens?: number;
      temperature?: number;
      signal?: AbortSignal;
    }
  ): Promise<void>;
  generate(messages: ChatMessage[], opts?: { maxTokens?: number; temperature?: number }): Promise<string>;
  dispose(): Promise<void>;
}

const GB = 1024 * 1024 * 1024;

/**
 * Model catalog.
 *
 * Size labels are the REAL q4f16 runtime footprints (download + compiled WebGPU
 * working set), not the bare weight download — the previous labels badly
 * understated Gemma. Order matters: the first entry is the picker's default.
 */
export const LOCAL_MODELS: LocalModel[] = [
  {
    id: "onnx-community/gemma-4-E2B-it-ONNX",
    label: "Gemma 4 E2B",
    size: "~1.5 GB",
    note: "Default. Best quality-for-size, Apache-2.0, strongest transformers.js support in its class.",
    dtype: "q4f16",
    family: "gemma",
    tier: "Default",
    approxBytes: Math.round(1.5 * GB)
  },
  {
    id: "onnx-community/Qwen3-0.6B-ONNX",
    label: "Qwen 3 0.6B",
    size: "~0.5 GB",
    note: "Low-memory fallback. Smallest option for constrained laptops and the safety net if larger models fail.",
    dtype: "q4f16",
    family: "causal-lm",
    tier: "Low-memory fallback",
    approxBytes: Math.round(0.5 * GB)
  },
  {
    id: "onnx-community/Qwen3-1.7B-ONNX",
    label: "Qwen 3 1.7B",
    size: "~1.1 GB",
    note: "Balanced Qwen option for extraction, summaries, and analyst Q&A.",
    dtype: "q4f16",
    family: "causal-lm",
    approxBytes: Math.round(1.1 * GB)
  },
  {
    id: "onnx-community/gemma-4-E4B-it-ONNX",
    label: "Gemma 4 E4B",
    size: "~3 GB",
    note: "High-memory / opt-in. Larger Gemma for richer prose; needs a recent dGPU.",
    dtype: "q4f16",
    family: "gemma",
    tier: "High-memory / opt-in",
    approxBytes: Math.round(3 * GB)
  },
  {
    id: "onnx-community/Qwen3-4B-Instruct-2507-ONNX",
    label: "Qwen 3 4B Instruct",
    size: "~2.8 GB",
    note: "High-memory / opt-in. Higher-quality structured output. Recent dGPU recommended.",
    dtype: "q4f16",
    family: "causal-lm",
    tier: "High-memory / opt-in",
    approxBytes: Math.round(2.8 * GB)
  },
  {
    id: "onnx-community/Qwen3-4B-Thinking-2507-ONNX",
    label: "Qwen 3 4B Thinking",
    size: "~2.8 GB",
    note: "High-memory / opt-in. Reasoning model. Slower, best for complex questions.",
    dtype: "q4f16",
    family: "causal-lm",
    tier: "High-memory / opt-in",
    approxBytes: Math.round(2.8 * GB)
  }
];

/** Best quality-for-size + Apache-2.0 + best transformers.js support in class. */
export const DEFAULT_LOCAL_MODEL = "onnx-community/gemma-4-E2B-it-ONNX";

/** The low-memory safety net used as the bottom of the fallback chain. */
export const FALLBACK_LOCAL_MODEL = "onnx-community/Qwen3-0.6B-ONNX";

export function findModel(id: string): LocalModel | undefined {
  return LOCAL_MODELS.find((model) => model.id === id);
}

type TransformersModule = typeof import("@huggingface/transformers");

let transformersModule: Promise<TransformersModule> | null = null;

function transformers(): Promise<TransformersModule> {
  transformersModule ??= import("@huggingface/transformers");
  return transformersModule;
}

// ---------------------------------------------------------------------------
// Device / memory probe
// ---------------------------------------------------------------------------

type GPUAdapterLike = {
  limits?: {
    maxStorageBufferBindingSize?: number;
    maxBufferSize?: number;
  };
};

type NavigatorGPULike = Navigator & {
  gpu?: { requestAdapter(): Promise<GPUAdapterLike | null> };
};

let deviceProbe: DeviceProbe | null = null;
let deviceProbePromise: Promise<DeviceProbe> | null = null;

function formatBytes(bytes?: number): string {
  if (!bytes || bytes <= 0) return "unknown";
  return `${(bytes / GB).toFixed(1)} GB`;
}

/**
 * Probe WebGPU + adapter limits once, caching the result. Safe to call from any
 * environment (returns a wasm-only probe when WebGPU is missing or throws).
 */
export async function probeDevice(): Promise<DeviceProbe> {
  if (deviceProbe) return deviceProbe;
  deviceProbePromise ??= (async (): Promise<DeviceProbe> => {
    if (typeof navigator === "undefined") {
      return {
        supported: false,
        recommendedDevice: "wasm",
        summary: "No navigator (WebGPU unavailable) · CPU/WASM"
      };
    }
    const nav = navigator as NavigatorGPULike;
    if (!nav.gpu) {
      return {
        supported: false,
        recommendedDevice: "wasm",
        summary: "WebGPU unavailable · CPU/WASM"
      };
    }
    try {
      const adapter = await nav.gpu.requestAdapter();
      if (!adapter) {
        return {
          supported: false,
          recommendedDevice: "wasm",
          summary: "No WebGPU adapter · CPU/WASM"
        };
      }
      const maxStorageBufferBindingSize = adapter.limits?.maxStorageBufferBindingSize;
      const maxBufferSize = adapter.limits?.maxBufferSize;
      const headroom = maxBufferSize ?? maxStorageBufferBindingSize;
      return {
        supported: true,
        recommendedDevice: "webgpu",
        maxStorageBufferBindingSize,
        maxBufferSize,
        summary: `WebGPU · ${formatBytes(headroom)} max buffer`
      };
    } catch {
      return {
        supported: false,
        recommendedDevice: "wasm",
        summary: "WebGPU probe failed · CPU/WASM"
      };
    }
  })();
  deviceProbe = await deviceProbePromise;
  return deviceProbe;
}

/** Last probe result without triggering a new probe (for synchronous UI reads). */
export function getDeviceProbe(): DeviceProbe | null {
  return deviceProbe;
}

/**
 * Does the probed device have enough single-buffer headroom for this model on
 * WebGPU? Uses the largest reported limit as the ceiling and the model's
 * approx working-set as the floor. Unknown limits => assume OK (let the load
 * attempt + fallback chain be the real arbiter rather than blocking eagerly).
 */
function webgpuFits(probe: DeviceProbe, model: LocalModel | undefined): boolean {
  if (!probe.supported) return false;
  const ceiling = probe.maxBufferSize ?? probe.maxStorageBufferBindingSize;
  if (!ceiling) return true;
  const need = model?.approxBytes;
  if (!need) return true;
  // A single weight buffer must fit under the binding limit. The compiled graph
  // allocates several buffers, but the largest single tensor is the hard wall
  // that triggers bad_alloc, so gate on ~70% of the model's working set.
  return ceiling >= need * 0.7;
}

// ---------------------------------------------------------------------------
// Engines
// ---------------------------------------------------------------------------

/**
 * Causal-LM engine via the generic text-generation pipeline. Used for Qwen3 and
 * any other plain decoder model. Unchanged behaviour from the original.
 */
class TransformersJSEngine implements LocalLLMEngine {
  constructor(
    public modelId: string,
    private pipe: TextGenerationPipeline
  ) {}

  async chat(
    messages: ChatMessage[],
    {
      onDelta,
      maxTokens = 900,
      temperature = 0.2,
      signal
    }: {
      onDelta: (text: string) => void;
      maxTokens?: number;
      temperature?: number;
      signal?: AbortSignal;
    }
  ): Promise<void> {
    if (signal?.aborted) throw new DOMException("Generation aborted", "AbortError");

    const { TextStreamer } = await transformers();
    const tokenizer = this.pipe.tokenizer as PreTrainedTokenizer;
    const streamer = new TextStreamer(tokenizer, {
      skip_prompt: true,
      skip_special_tokens: true,
      callback_function: (text: string) => {
        if (signal?.aborted) return;
        if (text) onDelta(text);
      }
    });

    await this.pipe(messages as unknown as string, {
      max_new_tokens: maxTokens,
      do_sample: temperature > 0,
      temperature,
      streamer,
      return_full_text: false
    });

    if (signal?.aborted) throw new DOMException("Generation aborted", "AbortError");
  }

  async generate(messages: ChatMessage[], opts?: { maxTokens?: number; temperature?: number }): Promise<string> {
    let text = "";
    await this.chat(messages, {
      maxTokens: opts?.maxTokens ?? 512,
      temperature: opts?.temperature ?? 0,
      onDelta: (delta) => {
        text += delta;
      }
    });
    return text;
  }

  async dispose(): Promise<void> {
    try {
      await this.pipe.dispose?.();
    } catch {
      // Older pipeline builds may not expose dispose().
    }
  }
}

type GemmaProcessor = {
  tokenizer?: PreTrainedTokenizer;
  apply_chat_template: (messages: unknown, options?: Record<string, unknown>) => unknown;
  batch_decode: (batch: unknown, options?: Record<string, unknown>) => string[];
};

type GemmaModel = {
  generate: (inputs: Record<string, unknown>) => Promise<unknown>;
  dispose?: () => Promise<void> | void;
};

/**
 * Gemma 4 (image-text-to-text) engine, mirroring the onnx-community model card:
 *   AutoProcessor.from_pretrained + Gemma4ForConditionalGeneration.from_pretrained,
 *   processor.apply_chat_template -> model.generate({ ...inputs, streamer }) ->
 *   processor.batch_decode(outputs.slice(...)).
 *
 * We pass text-only chat content. apply_chat_template (tokenize:true,
 * return_dict:true by default) yields { input_ids, attention_mask } tensors that
 * spread straight into generate(). Streaming is driven through the same
 * TextStreamer used elsewhere so the chat() contract is identical.
 */
class Gemma4Engine implements LocalLLMEngine {
  constructor(
    public modelId: string,
    private model: GemmaModel,
    private processor: GemmaProcessor
  ) {}

  async chat(
    messages: ChatMessage[],
    {
      onDelta,
      maxTokens = 900,
      temperature = 0.2,
      signal
    }: {
      onDelta: (text: string) => void;
      maxTokens?: number;
      temperature?: number;
      signal?: AbortSignal;
    }
  ): Promise<void> {
    if (signal?.aborted) throw new DOMException("Generation aborted", "AbortError");

    const { TextStreamer } = await transformers();

    // Gemma chat content is a list of typed parts; text-only here.
    const gemmaMessages = messages.map((message) => ({
      role: message.role,
      content: [{ type: "text", text: message.content }]
    }));

    const inputs = (await this.processor.apply_chat_template(gemmaMessages, {
      add_generation_prompt: true,
      // Gemma 4 / 3n are non-thinking by default in our usage.
      enable_thinking: false
    })) as Record<string, unknown>;

    const tokenizer = this.processor.tokenizer;
    if (!tokenizer) throw new Error("Gemma processor is missing its tokenizer.");

    const streamer = new TextStreamer(tokenizer, {
      skip_prompt: true,
      skip_special_tokens: true,
      callback_function: (text: string) => {
        if (signal?.aborted) return;
        if (text) onDelta(text);
      }
    });

    await this.model.generate({
      ...inputs,
      max_new_tokens: maxTokens,
      do_sample: temperature > 0,
      temperature,
      streamer
    });

    if (signal?.aborted) throw new DOMException("Generation aborted", "AbortError");
  }

  async generate(messages: ChatMessage[], opts?: { maxTokens?: number; temperature?: number }): Promise<string> {
    let text = "";
    await this.chat(messages, {
      maxTokens: opts?.maxTokens ?? 512,
      temperature: opts?.temperature ?? 0,
      onDelta: (delta) => {
        text += delta;
      }
    });
    return text;
  }

  async dispose(): Promise<void> {
    try {
      await this.model.dispose?.();
    } catch {
      // dispose() may be absent on some builds; ignore.
    }
  }
}

// ---------------------------------------------------------------------------
// Active-engine singleton + subscriber API (UNCHANGED public surface)
// ---------------------------------------------------------------------------

type EngineSubscriber = (engine: LocalLLMEngine | null) => void;

let activeEngine: LocalLLMEngine | null = null;
const subscribers = new Set<EngineSubscriber>();

export function getActiveEngine(): LocalLLMEngine | null {
  return activeEngine;
}

export function setActiveEngine(engine: LocalLLMEngine | null): void {
  activeEngine = engine;
  subscribers.forEach((subscriber) => subscriber(engine));
}

export function subscribeActiveEngine(subscriber: EngineSubscriber): () => void {
  subscribers.add(subscriber);
  return () => subscribers.delete(subscriber);
}

export async function disposeActiveEngine(): Promise<void> {
  const engine = activeEngine;
  if (!engine) return;
  setActiveEngine(null);
  await engine.dispose().catch(() => {});
}

// ---------------------------------------------------------------------------
// WebGPU detection (UNCHANGED signature) + loader with fallback chain
// ---------------------------------------------------------------------------

export async function detectWebGPU(): Promise<boolean> {
  return (await probeDevice()).supported;
}

/** Errors that mean "this model is too big / WebGPU couldn't allocate it". */
function isAllocationFailure(error: unknown): boolean {
  const message = (error instanceof Error ? error.message : String(error ?? "")).toLowerCase();
  return (
    message.includes("bad_alloc") ||
    message.includes("std::bad_alloc") ||
    message.includes("out of memory") ||
    message.includes("oom") ||
    message.includes("allocation") ||
    message.includes("failed to allocate") ||
    message.includes("session") || // onnxruntime session/InferenceSession creation failures
    message.includes("createbuffer") ||
    message.includes("buffer size") ||
    message.includes("exceeds the max") ||
    message.includes("webgpu") ||
    message.includes("device lost")
  );
}

type LoadAttempt = { modelId: string; device: "webgpu" | "wasm" };

/**
 * Build the ordered, de-duplicated, capped fallback chain.
 *
 * Order (per spec): chosen model first, then the universal safety net:
 *   <chosen>(webgpu/probe device) -> Gemma4-E2B(webgpu) -> Qwen3-0.6B(webgpu) -> Qwen3-0.6B(wasm)
 *
 * If the probe says WebGPU can't fit the chosen model (or WebGPU is absent), we
 * skip straight to safer/wasm options so we don't waste a guaranteed-failing
 * download+compile. The chain is always finite.
 */
function buildFallbackChain(chosenId: string, probe: DeviceProbe): LoadAttempt[] {
  const chosen = findModel(chosenId);
  const chain: LoadAttempt[] = [];

  if (probe.supported && webgpuFits(probe, chosen)) {
    chain.push({ modelId: chosenId, device: "webgpu" });
  }
  // Default Gemma E2B on WebGPU (covers "chosen was bigger and didn't fit").
  if (probe.supported && webgpuFits(probe, findModel(DEFAULT_LOCAL_MODEL))) {
    chain.push({ modelId: DEFAULT_LOCAL_MODEL, device: "webgpu" });
  }
  // Low-memory model on WebGPU.
  if (probe.supported) {
    chain.push({ modelId: FALLBACK_LOCAL_MODEL, device: "webgpu" });
  }
  // Universal terminal option: low-memory model on CPU/WASM (no WebGPU needed).
  chain.push({ modelId: FALLBACK_LOCAL_MODEL, device: "wasm" });

  // De-dup consecutive/repeat (modelId+device) pairs, then cap length.
  const seen = new Set<string>();
  const deduped = chain.filter((attempt) => {
    const key = `${attempt.modelId}@${attempt.device}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return deduped.slice(0, 4);
}

/** Low-level single-attempt load (no fallback). Throws on failure. */
async function loadOnce(
  attempt: LoadAttempt,
  onProgress: (progress: LoadProgress) => void
): Promise<LocalLLMEngine> {
  const model = findModel(attempt.modelId);
  const { env } = await transformers();

  env.allowLocalModels = false;
  env.useBrowserCache = true;

  type ProgressEvent = {
    status?: string;
    name?: string;
    file?: string;
    progress?: number;
    loaded?: number;
    total?: number;
  };

  const progressCallback = (raw: unknown) => {
    const progress = raw as ProgressEvent;
    const phase = progress.status ?? "loading";
    const file = progress.file ?? progress.name ?? "";
    const fraction =
      typeof progress.progress === "number"
        ? Math.max(0, Math.min(1, progress.progress / 100))
        : progress.loaded && progress.total
          ? progress.loaded / progress.total
          : undefined;
    onProgress({
      text: file ? `${phase} - ${file}` : phase,
      progress: fraction
    });
  };

  const dtype = model?.dtype ?? "q4f16";

  if (model?.family === "gemma") {
    // Gemma 4 ONNX (image-text-to-text) — dedicated processor + conditional-gen
    // class, per the onnx-community/gemma-4-E2B-it-ONNX model card.
    const { AutoProcessor, Gemma4ForConditionalGeneration } = await transformers();
    const [processor, gemmaModel] = await Promise.all([
      AutoProcessor.from_pretrained(attempt.modelId, {
        progress_callback: progressCallback
      }),
      Gemma4ForConditionalGeneration.from_pretrained(attempt.modelId, {
        dtype,
        device: attempt.device,
        progress_callback: progressCallback
      })
    ]);
    return new Gemma4Engine(
      attempt.modelId,
      gemmaModel as unknown as GemmaModel,
      processor as unknown as GemmaProcessor
    );
  }

  // Causal-LM path (Qwen3, etc.) via the generic text-generation pipeline.
  const { pipeline } = await transformers();
  const pipe = (await pipeline("text-generation", attempt.modelId, {
    device: attempt.device,
    dtype,
    progress_callback: progressCallback
  })) as TextGenerationPipeline;
  return new TransformersJSEngine(attempt.modelId, pipe);
}

/**
 * Load an on-device LLM with an automatic, capped fallback chain.
 *
 * Signature is UNCHANGED so useOndeviceLLM / ModelLoader keep working. The
 * returned engine's `.modelId` reflects the model that ACTUALLY loaded (which
 * may differ from `modelId` if a fallback kicked in). Each attempt surfaces a
 * clear status line through `onProgress` (e.g. "Trying a lighter model…").
 *
 * Fallback order:
 *   <chosen>(webgpu) -> Gemma4-E2B(webgpu) -> Qwen3-0.6B(webgpu) -> Qwen3-0.6B(wasm)
 * If all attempts fail, the final error is rethrown so the hook lands in a clean
 * terminal `error` state.
 */
export async function loadLocalLLM(
  modelId: string,
  onProgress: (progress: LoadProgress) => void
): Promise<LocalLLMEngine> {
  const probe = await probeDevice();

  // Release any prior pipeline/engine + WebGPU buffers BEFORE allocating a new
  // model. Un-freed allocations are the main trigger for std::bad_alloc on swap.
  await disposeActiveEngine();

  if (!probe.supported) {
    onProgress({ text: "WebGPU unavailable — using CPU/WASM (slower)…" });
  }

  const chain = buildFallbackChain(modelId, probe);
  let lastError: unknown = new Error("No load attempts were made.");

  for (let i = 0; i < chain.length; i++) {
    const attempt = chain[i];
    const model = findModel(attempt.modelId);
    const label = model?.label ?? attempt.modelId;

    if (i > 0) {
      // Announce the downgrade so the UI can show why the model changed.
      const reason =
        attempt.device === "wasm"
          ? `Trying a lighter model on CPU (${label})…`
          : `Trying a lighter model (${label})…`;
      onProgress({ text: reason, progress: 0 });
      // Make sure the failed attempt's buffers are gone before the next alloc.
      await disposeActiveEngine();
    } else {
      onProgress({ text: `Loading ${label}…`, progress: 0 });
    }

    try {
      const engine = await loadOnce(attempt, onProgress);
      return engine;
    } catch (error) {
      lastError = error;
      const recoverable = isAllocationFailure(error);
      const hasNext = i < chain.length - 1;
      if (!recoverable || !hasNext) {
        // Non-recoverable error, or we've exhausted the (capped) chain.
        break;
      }
      // else: loop continues to the next, safer attempt.
    }
  }

  // Clean terminal failure — rethrow for the hook's `error` state.
  await disposeActiveEngine();
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}
