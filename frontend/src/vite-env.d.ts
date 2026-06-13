/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE: string
  readonly VITE_DESKTOP_HANDOFF_URL?: string
  readonly VITE_MCP_BASE_URL?: string
  readonly VITE_KEYCLOAK_ISSUER?: string
  readonly VITE_KEYCLOAK_CLIENT_ID?: string
  readonly VITE_UMAMI_SRC?: string
  readonly VITE_UMAMI_WEBSITE_ID?: string
  readonly VITE_POSTHOG_KEY?: string
  readonly VITE_POSTHOG_HOST?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

// Analytics globals attached by the injected Umami script + PostHog snippet.
interface Window {
  umami?: {
    track: (
      eventOrPayload?: string | ((props: Record<string, unknown>) => Record<string, unknown>),
      data?: Record<string, unknown>
    ) => void
  }
  posthog?: {
    init: (key: string, opts: Record<string, unknown>) => void
    capture: (event: string, props?: Record<string, unknown>) => void
    identify: (id: string, props?: Record<string, unknown>) => void
    group: (type: string, key: string) => void
    opt_in_capturing: () => void
    opt_out_capturing: () => void
    reset: (resetId?: boolean) => void
    __loaded?: boolean
  }
}
