/**
 * Deterministic, theme-independent avatar colour — shared by the directory cards
 * (components/domain/Cards.tsx) AND the relationship graph (components/graph) so
 * the same person/org is always the same hue everywhere. A solid mid-tone puck
 * with a white initial is the Gmail/Linear pattern: gentle, scannable variety
 * that holds contrast in light and dark. Hues stay on the ecosystem palette and
 * are lightly desaturated so a long list / dense graph reads calm, not candy.
 */
export const AVATAR_HUES = [304, 330, 350, 24, 60, 145, 176, 224, 264] as const;

/** Stable hue (OKLCH degrees) for a seed string, or null for an empty seed. */
export function avatarHue(seed?: string | null): number | null {
  const s = (seed ?? "").trim();
  if (!s) return null;
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(h, 31) + s.charCodeAt(i)) >>> 0;
  return AVATAR_HUES[h % AVATAR_HUES.length];
}

/** OKLCH fill string for an avatar puck. Empty seed → a neutral grey. */
export function avatarColor(seed?: string | null): string {
  const hue = avatarHue(seed);
  return hue == null ? "oklch(0.5 0.02 280)" : `oklch(0.56 0.135 ${hue})`;
}
