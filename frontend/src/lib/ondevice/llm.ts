import type {
  PreTrainedTokenizer,
  TextGenerationPipeline
} from "@huggingface/transformers";

export type ModelStatus =
  | { status: "idle" }
  | { status: "loading"; progress: number; text: string }
  | { status: "ready"; modelId: string }
  | { status: "error"; error: string };

export type LocalModel = {
  id: string;
  label: string;
  size: string;
  note: string;
  dtype: "q4f16" | "fp16" | "int8" | "q8";
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

export const LOCAL_MODELS: LocalModel[] = [
  {
    id: "onnx-community/Qwen3-1.7B-ONNX",
    label: "Qwen 3 1.7B",
    size: "~1.1 GB",
    note: "Default for extraction, summaries, and analyst Q&A.",
    dtype: "q4f16"
  },
  {
    id: "onnx-community/Qwen3-0.6B-ONNX",
    label: "Qwen 3 0.6B",
    size: "~0.5 GB",
    note: "Smallest Qwen option for lower-memory laptops.",
    dtype: "q4f16"
  },
  {
    id: "onnx-community/gemma-4-E2B-it-ONNX",
    label: "Gemma 4 E2B",
    size: "~0.5 GB",
    note: "Fast first load, good conversational fallback.",
    dtype: "q4f16"
  },
  {
    id: "onnx-community/gemma-4-E4B-it-ONNX",
    label: "Gemma 4 E4B",
    size: "~1.5 GB",
    note: "Larger Gemma option for richer prose.",
    dtype: "q4f16"
  },
  {
    id: "onnx-community/Qwen3-4B-Instruct-2507-ONNX",
    label: "Qwen 3 4B Instruct",
    size: "~2.4 GB",
    note: "Higher-quality structured output. Recent dGPU recommended.",
    dtype: "q4f16"
  },
  {
    id: "onnx-community/Qwen3-4B-Thinking-2507-ONNX",
    label: "Qwen 3 4B Thinking",
    size: "~2.4 GB",
    note: "Reasoning model. Slower, best for complex questions.",
    dtype: "q4f16"
  }
];

export const DEFAULT_LOCAL_MODEL = "onnx-community/Qwen3-1.7B-ONNX";

export function findModel(id: string): LocalModel | undefined {
  return LOCAL_MODELS.find((model) => model.id === id);
}

type TransformersModule = typeof import("@huggingface/transformers");

let transformersModule: Promise<TransformersModule> | null = null;

function transformers(): Promise<TransformersModule> {
  transformersModule ??= import("@huggingface/transformers");
  return transformersModule;
}

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

export async function detectWebGPU(): Promise<boolean> {
  if (typeof navigator === "undefined") return false;
  const nav = navigator as Navigator & {
    gpu?: { requestAdapter(): Promise<unknown> };
  };
  if (!nav.gpu) return false;
  try {
    return Boolean(await nav.gpu.requestAdapter());
  } catch {
    return false;
  }
}

export async function loadLocalLLM(
  modelId: string,
  onProgress: (progress: LoadProgress) => void
): Promise<LocalLLMEngine> {
  const model = findModel(modelId);
  const { env, pipeline } = await transformers();

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

  const pipe = (await pipeline("text-generation", modelId, {
    device: "webgpu",
    dtype: model?.dtype ?? "q4f16",
    progress_callback: (raw: unknown) => {
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
    }
  })) as TextGenerationPipeline;

  return new TransformersJSEngine(modelId, pipe);
}
