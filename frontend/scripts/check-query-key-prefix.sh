#!/usr/bin/env bash
# Phase-4 §6 query-key guard (zero-dep, no eslint-plugin needed).
#
# Fails if any `queryKey:` is an INLINE ARRAY LITERAL whose first element is not
# the tenant ("t") or global ("g") sentinel — i.e. a raw key that would leak
# across workspaces because it carries no active-tenant prefix. Every workspace-
# scoped key must come from the `useQueryKeys()` factory (lib/queryKeys.ts);
# tenant-independent keys must come from `userKey` / `globalKey` (the "g"
# sentinel). See the frontend review: this catches inline literals only — the
# real enforcement is the factory being the single key-construction path.
#
# Portable: uses only POSIX ERE (grep -E), so it works with both BSD/macOS grep
# (no -P) and GNU grep. Two passes: find every inline `queryKey: [ <str>` then
# subtract the ones whose first element is the "t" or "g" sentinel.
#
# Run from frontend/:  bash scripts/check-query-key-prefix.sh
# Wire into CI alongside `npm run lint` and `npm run build`.
set -euo pipefail

SRC_DIR="${1:-src}"

# Pass 1: every `queryKey:` immediately followed by an array literal whose first
# element is a quoted string, e.g.  queryKey: ["person", id]  /  queryKey: ['t', x]
all_inline="$(grep -rEn "queryKey:[[:space:]]*\[[[:space:]]*['\"]" "$SRC_DIR" \
  --include='*.ts' --include='*.tsx' || true)"

# Pass 2: drop the allowed sentinels (first element is "t" or "g", single OR
# double quoted). What remains are raw, un-prefixed keys → violations.
violations="$(printf '%s\n' "$all_inline" \
  | grep -vE "queryKey:[[:space:]]*\[[[:space:]]*['\"](t|g)['\"]" || true)"

# Strip a possible empty line from the printf when there were no matches at all.
violations="$(printf '%s' "$violations" | sed '/^$/d')"

if [ -n "$violations" ]; then
  printf '%s\n' "$violations" >&2
  echo "" >&2
  echo "ERROR: the queryKey array literal(s) above are missing the tenant prefix." >&2
  echo "       Build the key via useQueryKeys() (qk.*) — or, if it is deliberately" >&2
  echo "       tenant-independent, via userKey / globalKey (\"g\"). Raw keys leak" >&2
  echo "       data across workspaces (Phase-4 §6)." >&2
  exit 1
fi

echo "query-key prefix guard: OK (no un-prefixed inline queryKey literals in $SRC_DIR)"
