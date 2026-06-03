/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE: string
  readonly VITE_DESKTOP_HANDOFF_URL?: string
  // Gates the FREE-tier on-device AI foundation (WebGPU LLM + embeddings +
  // local IndexedDB store). "true" enables it; anything else (default) keeps it off.
  readonly VITE_ENABLE_LOCAL_AI?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
