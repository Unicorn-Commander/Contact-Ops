import { env, pipeline, type FeatureExtractionPipeline } from "@huggingface/transformers";

type EmbedRequest = {
  id: string;
  type: "embed";
  texts: string[];
};

type EmbedResponse =
  | { id: string; type: "result"; embeddings: number[][] }
  | { id: string; type: "error"; error: string };

let extractorPromise: Promise<FeatureExtractionPipeline> | null = null;

function getExtractor(): Promise<FeatureExtractionPipeline> {
  env.allowLocalModels = false;
  env.useBrowserCache = true;
  extractorPromise ??= pipeline("feature-extraction", "Xenova/all-MiniLM-L6-v2") as Promise<FeatureExtractionPipeline>;
  return extractorPromise;
}

self.onmessage = (event: MessageEvent<EmbedRequest>) => {
  const request = event.data;
  if (request.type !== "embed") return;

  void (async () => {
    try {
      const extractor = await getExtractor();
      const embeddings: number[][] = [];
      for (const text of request.texts) {
        const output = await extractor(text, { pooling: "mean", normalize: true });
        const data = "data" in output ? Array.from(output.data as Float32Array) : [];
        embeddings.push(data);
      }
      self.postMessage({ id: request.id, type: "result", embeddings } satisfies EmbedResponse);
    } catch (error) {
      self.postMessage({
        id: request.id,
        type: "error",
        error: error instanceof Error ? error.message : String(error)
      } satisfies EmbedResponse);
    }
  })();
};
