export const env = {
  mcpBaseUrl:
    import.meta.env.VITE_MCP_BASE_URL ?? "https://mcp.contacts.magicunicorn.dev",
  keycloakIssuer:
    import.meta.env.VITE_KEYCLOAK_ISSUER ?? "https://auth.magicunicorn.dev/realms/uchub",
  keycloakClientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? "contact-ops-app",
  // Cross-app suite deep-links (e.g. the Project-Ops "Related work" link).
  // Per-deployment and OPTIONAL: empty is the SAFE default and the link degrades
  // to plain text, so a self-host or white-label cell that does not run
  // Project-Ops (or runs it on its own domain) never points at a UC-operated
  // host. UC cells set VITE_PROJECTS_BASE_URL at build time (dogfood ->
  // projects.magicunicorn.dev, prod -> projects.unicorncommander.ai).
  projectsBaseUrl: import.meta.env.VITE_PROJECTS_BASE_URL ?? "",
  // Analytics — empty by default = DORMANT (loaders no-op). Point at the
  // self-hosted Umami + PostHog (centerdeep/commander VPS) to enable. These bake
  // at BUILD time, so flipping analytics on is a frontend rebuild. Consent-gated
  // (opt-in) regardless.
  umamiSrc: import.meta.env.VITE_UMAMI_SRC ?? "",
  umamiWebsiteId: import.meta.env.VITE_UMAMI_WEBSITE_ID ?? "",
  posthogKey: import.meta.env.VITE_POSTHOG_KEY ?? "",
  posthogHost: import.meta.env.VITE_POSTHOG_HOST ?? ""
};

// Request only standard OIDC scopes. The app's permission scopes (person:*,
// org:*, tag:*, voice:*, etc.) and uc_uid/tenant claims are granted server-side
// as default client scopes on contact-ops-app, so they're always in the token
// without the SPA enumerating them (and requesting unconfigured scopes triggers
// invalid_scope under strict realms).
export const oidcScopes = ["openid", "profile", "email"].join(" ");
