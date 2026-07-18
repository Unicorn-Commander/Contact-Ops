import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Vitest config for the Contact-Ops SPA. Kept separate from vite.config.ts so
// the production build config stays free of test-only settings. Reuses the same
// "@" alias so tests import exactly like app code.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src")
    }
  },
  test: {
    environment: "jsdom",
    // Explicit imports (import { describe } from "vitest") — no injected globals,
    // so ESLint's no-undef stays honest and tsc needs no ambient global types.
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Playwright specs live in e2e/ and must not be picked up by vitest.
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
    css: false,
    restoreMocks: true
  }
});
