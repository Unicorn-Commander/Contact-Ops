import { expect, test, type Page } from "@playwright/test";

/**
 * Smoke E2E: login → dashboard → people.
 *
 * Two auth modes are supported so the same spec runs against either stack:
 *
 *  - STANDALONE_MODE (the default `docker-compose.yml` / `npm run dev`): no
 *    OAuth2, the app renders the dashboard directly.
 *  - Real Keycloak (the self-contained compose with a realm): set E2E_USERNAME
 *    and E2E_PASSWORD and the spec drives the hosted login form.
 *
 * Not run in CI yet (no browsers on the runners) — see playwright.config.ts.
 */

const USERNAME = process.env.E2E_USERNAME;
const PASSWORD = process.env.E2E_PASSWORD;

async function loginIfNeeded(page: Page): Promise<void> {
  // Keycloak hosts the login form on its own origin (…/realms/…/protocol/openid-connect/auth).
  const onKeycloak = /\/realms\/.+\/protocol\/openid-connect/.test(page.url());
  if (!onKeycloak) return; // STANDALONE_MODE — already authenticated.

  if (!USERNAME || !PASSWORD) {
    test.skip(true, "Redirected to Keycloak but E2E_USERNAME/E2E_PASSWORD are not set.");
    return;
  }

  await page.getByLabel(/username|email/i).fill(USERNAME);
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /sign in|log in/i }).click();
}

test("login → dashboard → people", async ({ page }) => {
  await page.goto("/");
  await loginIfNeeded(page);

  // Dashboard: the command-center hero headline is stable copy on the landing.
  await expect(
    page.getByRole("heading", { name: /relationship graph at a glance/i })
  ).toBeVisible();

  // Navigate to People via the sidebar/nav link.
  await page.getByRole("link", { name: /^people$/i }).first().click();
  await expect(page).toHaveURL(/\/people/);

  // People directory chrome is present (heading + search box).
  await expect(page.getByRole("heading", { name: /^people$/i })).toBeVisible();
  await expect(page.getByRole("searchbox", { name: /search people/i })).toBeVisible();
});
