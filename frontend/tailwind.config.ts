import type { Config } from "tailwindcss";

const oklch = (token: string) => `oklch(var(${token}) / <alpha-value>)`;
const production = process.env.NODE_ENV === "production";
const content = [
  "./index.html",
  "./src/**/*.{js,ts,jsx,tsx}",
  ...(production
    ? [
        "!./src/design-system/Showcase.tsx",
        "!./src/design-system/ShowcaseV2.tsx",
        "!./src/design-system/InboxStressTest.tsx",
        "!./src/design-system/GraphLabelStressTest.tsx"
      ]
    : [])
];

const config: Config = {
  darkMode: ["class"],
  content,
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"]
      },
      fontSize: {
        11: ["var(--text-11)", { lineHeight: "var(--leading-11)" }],
        12: ["var(--text-12)", { lineHeight: "var(--leading-12)" }],
        13: ["var(--text-13)", { lineHeight: "var(--leading-13)" }],
        14: ["var(--text-14)", { lineHeight: "var(--leading-14)" }],
        16: ["var(--text-16)", { lineHeight: "var(--leading-16)" }],
        18: ["var(--text-18)", { lineHeight: "var(--leading-18)" }],
        20: ["var(--text-20)", { lineHeight: "var(--leading-20)" }],
        24: ["var(--text-24)", { lineHeight: "var(--leading-24)" }],
        30: ["var(--text-30)", { lineHeight: "var(--leading-30)" }],
        36: ["var(--text-36)", { lineHeight: "var(--leading-36)" }]
      },
      spacing: {
        "co-2": "var(--co-space-2)",
        "co-4": "var(--co-space-4)",
        "co-6": "var(--co-space-6)",
        "co-8": "var(--co-space-8)",
        "co-12": "var(--co-space-12)",
        "co-16": "var(--co-space-16)",
        "co-20": "var(--co-space-20)",
        "co-24": "var(--co-space-24)",
        "co-32": "var(--co-space-32)",
        "co-40": "var(--co-space-40)",
        "co-56": "var(--co-space-56)",
        "co-72": "var(--co-space-72)"
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-md)",
        xl: "var(--radius-lg)",
        full: "var(--radius-pill)"
      },
      boxShadow: {
        co1: "var(--shadow-1)",
        co2: "var(--shadow-2)",
        co3: "var(--shadow-3)",
        co4: "var(--shadow-4)"
      },
      colors: {
        border: oklch("--border"),
        input: oklch("--input"),
        ring: oklch("--ring"),
        background: oklch("--background"),
        foreground: oklch("--foreground"),
        link: oklch("--link"),
        primary: {
          DEFAULT: oklch("--primary"),
          foreground: oklch("--primary-foreground")
        },
        "accent-fuchsia": {
          DEFAULT: oklch("--accent-fuchsia"),
          foreground: oklch("--accent-fuchsia-foreground")
        },
        secondary: {
          DEFAULT: oklch("--secondary"),
          foreground: oklch("--secondary-foreground")
        },
        muted: {
          DEFAULT: oklch("--muted"),
          foreground: oklch("--muted-foreground")
        },
        accent: {
          DEFAULT: oklch("--accent"),
          foreground: oklch("--accent-foreground")
        },
        destructive: {
          DEFAULT: oklch("--destructive"),
          foreground: oklch("--destructive-foreground")
        },
        success: {
          DEFAULT: oklch("--success"),
          foreground: oklch("--success-foreground")
        },
        warning: {
          DEFAULT: oklch("--warning"),
          foreground: oklch("--warning-foreground")
        },
        info: {
          DEFAULT: oklch("--info"),
          foreground: oklch("--info-foreground")
        },
        card: {
          DEFAULT: oklch("--card"),
          foreground: oklch("--card-foreground")
        },
        popover: {
          DEFAULT: oklch("--popover"),
          foreground: oklch("--popover-foreground")
        },
        confidence: {
          emerald: oklch("--confidence-emerald"),
          sky: oklch("--confidence-sky"),
          amber: oklch("--confidence-amber"),
          rose: oklch("--confidence-rose")
        }
      },
      transitionTimingFunction: {
        standard: "var(--ease-standard)",
        snappy: "var(--ease-snappy)",
        gentle: "var(--ease-gentle)"
      },
      keyframes: {
        "fade-in": {
          from: { opacity: 0, transform: "translateY(4px)" },
          to: { opacity: 1, transform: "translateY(0)" }
        },
        "fade-in-scale": {
          from: { opacity: 0, transform: "scale(0.96)" },
          to: { opacity: 1, transform: "scale(1)" }
        },
        "slide-up": {
          from: { opacity: 0, transform: "translateY(8px)" },
          to: { opacity: 1, transform: "translateY(0)" }
        },
        "pulse-subtle": {
          "0%, 100%": { opacity: 1 },
          "50%": { opacity: 0.7 }
        },
        shimmer: {
          from: { backgroundPosition: "-200% 0" },
          to: { backgroundPosition: "200% 0" }
        }
      },
      animation: {
        "fade-in": "fade-in var(--motion-base, 200ms) var(--motion-ease-out, cubic-bezier(0, 0, 0.2, 1)) both",
        "fade-in-scale": "fade-in-scale var(--motion-base, 200ms) var(--motion-ease-out, cubic-bezier(0, 0, 0.2, 1)) both",
        "slide-up": "slide-up var(--motion-base, 200ms) var(--motion-ease-out, cubic-bezier(0, 0, 0.2, 1)) both",
        "pulse-subtle": "pulse-subtle 2s ease-in-out infinite",
        shimmer: "shimmer 1.8s ease-in-out infinite"
      }
    }
  },
  plugins: []
};

export default config;
