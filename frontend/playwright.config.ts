import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E config for Contact-Ops.
 *
 * Runs against a RUNNING stack — by default the self-contained compose
 * (`docker compose -f docker-compose.selfcontained.yml up`), which serves the
 * SPA and an MCP backend behind Traefik. Point at it with E2E_BASE_URL.
 *
 * This is intentionally NOT wired into CI yet (no browsers installed on the
 * Woodpecker runners). Run locally with:
 *
 *   E2E_BASE_URL=https://contacts.localhost npm run test:e2e
 *
 * or, against a plain `npm run dev` in STANDALONE_MODE:
 *
 *   E2E_BASE_URL=http://localhost:5173 npm run test:e2e
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    // The self-contained stack may use a self-signed Traefik cert on *.localhost.
    ignoreHTTPSErrors: true
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ]
});
