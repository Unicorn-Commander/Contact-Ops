type WorkerResponse =
  | { id: string; type: "result"; embeddings: number[][] }
  | { id: string; type: "error"; error: string };

let worker: Worker | null = null;

function getWorker(): Worker {
  worker ??= new Worker(new URL("./embeddings.worker.ts", import.meta.url), { type: "module" });
  return worker;
}

function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `embed_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

export function cosine(a: Float32Array, b: Float32Array): number {
  const length = Math.min(a.length, b.length);
  if (length === 0) return 0;
  let dot = 0;
  let normA = 0;
  let normB = 0;
  for (let index = 0; index < length; index += 1) {
    dot += a[index] * b[index];
    normA += a[index] * a[index];
    normB += b[index] * b[index];
  }
  if (normA === 0 || normB === 0) return 0;
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}

export function embed(texts: string[]): Promise<Float32Array[]> {
  const id = uuid();
  const activeWorker = getWorker();

  return new Promise((resolve, reject) => {
    const onMessage = (event: MessageEvent<WorkerResponse>) => {
      if (event.data.id !== id) return;
      activeWorker.removeEventListener("message", onMessage);
      if (event.data.type === "error") {
        reject(new Error(event.data.error));
        return;
      }
      resolve(event.data.embeddings.map((embedding) => new Float32Array(embedding)));
    };

    activeWorker.addEventListener("message", onMessage);
    activeWorker.postMessage({ id, type: "embed", texts });
  });
}
