module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended"
  ],
  ignorePatterns: ["dist", "build", "node_modules", "eslint-local-rules", "scripts"],
  parser: "@typescript-eslint/parser",
  plugins: ["react-refresh"],
  rules: {
    "react-refresh/only-export-components": "off",
    "@typescript-eslint/no-explicit-any": "off"
  },
  overrides: [
    {
      // Tooling + E2E files run under Node (process.env, __dirname), not the browser.
      files: ["*.config.ts", "vitest.config.ts", "playwright.config.ts", "e2e/**/*.ts"],
      env: { node: true, browser: false }
    },
    {
      // Vitest unit/component tests run in jsdom with Node-side test globals.
      files: ["src/**/*.{test,spec}.{ts,tsx}", "src/test/**/*.{ts,tsx}"],
      env: { node: true, browser: true }
    }
  ]
};
