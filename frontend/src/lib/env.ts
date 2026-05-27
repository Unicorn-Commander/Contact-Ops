export const env = {
  mcpBaseUrl:
    import.meta.env.VITE_MCP_BASE_URL ?? "https://mcp.contacts.magicunicorn.dev",
  keycloakIssuer:
    import.meta.env.VITE_KEYCLOAK_ISSUER ?? "https://auth.magicunicorn.dev/realms/uchub",
  keycloakClientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? "contact-ops-app"
};

// Request only standard OIDC scopes. The app's permission scopes (person:*,
// org:*, tag:*, voice:*, etc.) and uc_uid/tenant claims are granted server-side
// as default client scopes on contact-ops-app, so they're always in the token
// without the SPA enumerating them (and requesting unconfigured scopes triggers
// invalid_scope under strict realms).
export const oidcScopes = ["openid", "profile", "email"].join(" ");
