export const env = {
  mcpBaseUrl: import.meta.env.VITE_MCP_BASE_URL ?? "http://localhost:8501",
  keycloakIssuer:
    import.meta.env.VITE_KEYCLOAK_ISSUER ?? "https://auth.unicorncommander.ai/realms/uchub",
  keycloakClientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? "contact-ops-app"
};

export const oidcScopes = [
  "openid",
  "profile",
  "email",
  "user-attributes",
  "person:read",
  "person:write",
  "org:read",
  "org:write",
  "field:propose_set",
  "relationship:propose",
  "relationship:apply",
  "tag:apply",
  "voice:match"
].join(" ");
