export type ThemeMode = "dark" | "light";
export type DensityMode = "compact" | "default" | "comfortable";
export type ConfidenceTier = "high" | "good" | "review" | "low";
export type ItemStatus = "proposed" | "applied" | "rejected" | "snoozed";
export type CSSVarStyle = Record<`--${string}`, string | number>;

export const itemStatusConfig: Record<ItemStatus, { label: string; tone: string }> = {
  proposed: { label: "Proposed", tone: "amber" },
  applied: { label: "Applied", tone: "emerald" },
  rejected: { label: "Rejected", tone: "rose" },
  snoozed: { label: "Snoozed", tone: "sky" }
};

export const colorRamps = {
  neutral: {
    50: "oklch(0.986 0.004 76)",
    100: "oklch(0.956 0.005 76)",
    200: "oklch(0.904 0.006 76)",
    300: "oklch(0.806 0.007 76)",
    500: "oklch(0.548 0.009 76)",
    700: "oklch(0.334 0.009 76)",
    900: "oklch(0.182 0.009 76)",
    950: "oklch(0.138 0.009 76)"
  },
  brand: {
    50: "oklch(0.972 0.031 318)",
    100: "oklch(0.925 0.056 318)",
    200: "oklch(0.841 0.096 318)",
    300: "oklch(0.739 0.139 318)",
    500: "oklch(0.617 0.190 318)",
    700: "oklch(0.492 0.170 318)",
    900: "oklch(0.355 0.120 318)",
    950: "oklch(0.244 0.082 318)"
  },
  emerald: {
    50: "oklch(0.966 0.035 154)",
    100: "oklch(0.910 0.068 154)",
    200: "oklch(0.804 0.119 154)",
    300: "oklch(0.714 0.156 154)",
    500: "oklch(0.634 0.175 154)",
    700: "oklch(0.503 0.137 154)",
    900: "oklch(0.384 0.095 154)",
    950: "oklch(0.267 0.067 154)"
  },
  sky: {
    50: "oklch(0.968 0.027 226)",
    100: "oklch(0.914 0.056 226)",
    200: "oklch(0.826 0.091 226)",
    300: "oklch(0.728 0.126 226)",
    500: "oklch(0.637 0.153 226)",
    700: "oklch(0.516 0.128 226)",
    900: "oklch(0.394 0.089 226)",
    950: "oklch(0.285 0.062 226)"
  },
  amber: {
    50: "oklch(0.976 0.048 84)",
    100: "oklch(0.926 0.086 84)",
    200: "oklch(0.842 0.126 84)",
    300: "oklch(0.756 0.150 84)",
    500: "oklch(0.676 0.165 84)",
    700: "oklch(0.548 0.126 84)",
    900: "oklch(0.410 0.086 84)",
    950: "oklch(0.302 0.063 84)"
  },
  rose: {
    50: "oklch(0.958 0.033 18)",
    100: "oklch(0.896 0.065 18)",
    200: "oklch(0.794 0.112 18)",
    300: "oklch(0.694 0.155 18)",
    500: "oklch(0.606 0.199 18)",
    700: "oklch(0.506 0.174 18)",
    900: "oklch(0.390 0.121 18)",
    950: "oklch(0.286 0.085 18)"
  }
} as const;

export const typeScale = {
  11: { size: "0.6875rem", lineHeight: "1rem" },
  12: { size: "0.75rem", lineHeight: "1rem" },
  13: { size: "0.8125rem", lineHeight: "1.125rem" },
  14: { size: "0.875rem", lineHeight: "1.25rem" },
  16: { size: "1rem", lineHeight: "1.5rem" },
  18: { size: "1.125rem", lineHeight: "1.625rem" },
  20: { size: "1.25rem", lineHeight: "1.75rem" },
  24: { size: "1.5rem", lineHeight: "2rem" },
  30: { size: "1.875rem", lineHeight: "2.375rem" },
  36: { size: "2.25rem", lineHeight: "2.75rem" }
} as const;

export const spacingScale = {
  2: "2px",
  4: "4px",
  6: "6px",
  8: "8px",
  12: "12px",
  16: "16px",
  20: "20px",
  24: "24px",
  32: "32px",
  40: "40px",
  56: "56px",
  72: "72px"
} as const;

export const radiusScale = {
  small: "4px",
  medium: "8px",
  large: "12px",
  pill: "999px"
} as const;

export const motionTokens = {
  durations: {
    instant: 75,
    quick: 150,
    base: 200,
    deliberate: 300,
    slow: 450
  },
  springs: {
    gentle: { type: "spring", stiffness: 170, damping: 24, mass: 1, bounce: 0.04 },
    default: { type: "spring", stiffness: 260, damping: 30, mass: 1, bounce: 0.06 },
    snappy: { type: "spring", stiffness: 420, damping: 34, mass: 0.9, bounce: 0.04 },
    bouncy: { type: "spring", stiffness: 360, damping: 22, mass: 0.8, bounce: 0.1 }
  }
} as const;

export const motionConfig = {
  durations: {
    fast: 120,
    base: 200,
    slow: 360
  } as const,
  easings: {
    easeOut: "cubic-bezier(0, 0, 0.2, 1)",
    easeInOut: "cubic-bezier(0.4, 0, 0.2, 1)"
  } as const
} as const;

export const confidenceTiers: Record<
  ConfidenceTier,
  {
    min: number;
    label: string;
    tone: "emerald" | "sky" | "amber" | "rose";
    cssVar: string;
    solid: boolean;
  }
> = {
  high: {
    min: 0.9,
    label: "High",
    tone: "emerald",
    cssVar: "var(--confidence-emerald)",
    solid: true
  },
  good: {
    min: 0.75,
    label: "Good",
    tone: "sky",
    cssVar: "var(--confidence-sky)",
    solid: true
  },
  review: {
    min: 0.5,
    label: "Review",
    tone: "amber",
    cssVar: "var(--confidence-amber)",
    solid: false
  },
  low: {
    min: 0,
    label: "Low",
    tone: "rose",
    cssVar: "var(--confidence-rose)",
    solid: false
  }
};

export function getConfidenceTier(value: number): ConfidenceTier {
  if (value >= confidenceTiers.high.min) return "high";
  if (value >= confidenceTiers.good.min) return "good";
  if (value >= confidenceTiers.review.min) return "review";
  return "low";
}

export function formatConfidence(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

export function agentHueFromSlug(slug: string) {
  let hash = 2166136261;
  for (let index = 0; index < slug.length; index += 1) {
    hash ^= slug.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash % 360);
}

export function agentColorVars(slug: string): CSSVarStyle {
  const hue = agentHueFromSlug(slug);
  return {
    "--agent-hue": hue,
    "--agent-color": `oklch(0.68 0.15 ${hue})`,
    "--agent-color-muted": `oklch(0.68 0.15 ${hue} / 0.16)`
  };
}

const THEME_STORAGE_KEY = "theme";

/** The OS-level color scheme. Falls back to dark when matchMedia is unavailable. */
export function getSystemTheme(): ThemeMode {
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** The user's explicit choice, or null when they're following the OS preference. */
export function getStoredTheme(): ThemeMode | null {
  if (typeof window === "undefined") return null;
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : null;
}

/** Theme to render: the explicit choice if set, otherwise the OS preference. */
export function getThemePreference(): ThemeMode {
  return getStoredTheme() ?? getSystemTheme();
}

/** Apply a theme to the document. Does NOT persist — used for OS-driven application. */
export function applyThemePreference(theme: ThemeMode) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.classList.toggle("dark", theme === "dark");
  root.classList.toggle("light", theme === "light");
  root.style.colorScheme = theme;
}

/** Persist an explicit user choice and apply it. */
export function setThemePreference(theme: ThemeMode) {
  if (typeof window !== "undefined") window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  applyThemePreference(theme);
}

/**
 * Subscribe to OS color-scheme changes. The callback receives the new system
 * theme; callers should ignore it when the user has an explicit stored choice.
 * Returns an unsubscribe function.
 */
export function subscribeSystemTheme(callback: (theme: ThemeMode) => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = (event: MediaQueryListEvent) => callback(event.matches ? "dark" : "light");
  query.addEventListener("change", handler);
  return () => query.removeEventListener("change", handler);
}
