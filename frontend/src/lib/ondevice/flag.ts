// Feature flag for the FREE-tier on-device AI foundation.
//
// On-device AI (WebGPU LLM + embeddings + local IndexedDB store) is gated behind
// VITE_ENABLE_LOCAL_AI so the heavyweight @huggingface/transformers runtime and
// the local-only contact store stay completely inert in builds that don't opt in.
// Defaults to false: nothing on-device loads, downloads, or registers unless the
// flag is explicitly turned on at build time.
//
// Vite inlines import.meta.env.* at build time, so this is statically analyzable
// and dead-code-eliminates the on-device modules when the flag is off.
export function isLocalAiEnabled(): boolean {
  return import.meta.env.VITE_ENABLE_LOCAL_AI === "true";
}
