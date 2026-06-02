/**
 * OPTIONAL ESLint rule (not wired into .eslintrc.cjs by default).
 *
 * Fails if a `useQuery`/`useInfiniteQuery` `queryKey` is an ARRAY LITERAL that
 * does not start with the tenant ("t") or global ("g") sentinel. Enforces
 * Phase-4 §6: every workspace-scoped React Query key must carry the active
 * tenant id so a `queryClient.clear()` switch cannot leak cross-workspace data.
 *
 * PASS:  queryKey: qk.person(id)            // factory call
 *        queryKey: key                       // identifier (assumed factory-built)
 *        queryKey: userKey.tenants           // explicit global allow-list
 *        queryKey: ["t", tenantId, ...]      // tenant sentinel
 *        queryKey: ["g", ...]                // global sentinel
 * FAIL:  queryKey: ["person", id]            // raw array, no tenant prefix
 *
 * REGISTRATION (flagged by the design + the review): eslint 8's legacy
 * `.eslintrc` cannot register an inline plugin OBJECT — `plugins: ["local"]`
 * expects a resolvable package `eslint-plugin-local`. To actually USE this rule,
 * either add the `eslint-plugin-local-rules` dev-dep (drop this file in
 * `./eslint-local-rules/index.js`, set `plugins: ["local-rules"]`, rule
 * `local-rules/query-key-tenant-prefix`) or run eslint with
 * `--rulesdir ./eslint-local-rules` (deprecated but works in 8.57).
 *
 * Because mis-wiring this would BREAK `npm run lint` (the build gate), the
 * shipped guard is instead the zero-dep `scripts/check-query-key-prefix.sh`,
 * which is equivalent for inline array literals. This file is provided for teams
 * that prefer the plugin path. The rule logic below is final.
 *
 * Known blind spot (review SEV-4): identifiers / member-expressions / call-
 * expressions pass unconditionally, so `queryKey: someRawArrayVar` is NOT caught.
 * Real enforcement = the factory being the only key-construction path.
 */
"use strict";

module.exports = {
  meta: {
    type: "problem",
    docs: {
      description:
        "queryKey must use the tenant-prefixed key factory (qk.*) or an allow-listed global key"
    },
    schema: []
  },
  create(context) {
    return {
      Property(node) {
        const key = node.key;
        const isQueryKey =
          (key.type === "Identifier" && key.name === "queryKey") ||
          (key.type === "Literal" && key.value === "queryKey");
        if (!isQueryKey) return;

        const v = node.value;

        // Array literal → must start with "t" (tenant) or "g" (global) sentinel.
        if (v.type === "ArrayExpression") {
          const first = v.elements[0];
          const ok =
            first &&
            first.type === "Literal" &&
            (first.value === "t" || first.value === "g");
          if (!ok) {
            context.report({
              node: v,
              message:
                "queryKey array must come from the tenant key factory (qk.*) or globalKey()/userKey. Raw keys leak across workspaces."
            });
          }
        }
        // qk.foo(...) / qk.foo / userKey.foo / globalKey(...) / identifiers are
        // accepted (assumed factory-built). See known blind spot above.
      }
    };
  }
};
