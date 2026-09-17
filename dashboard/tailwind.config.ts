import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#05070b", 900: "#0a0e15", 800: "#111827", 700: "#1b2333" },
        state: {
          baseline: "#38bdf8",
          toxin: "#f59e0b",
          drug: "#34d399",
          radiation: "#c084fc",
          offline: "#64748b",
          collapsed: "#ef4444",
        },
      },
      fontFamily: { mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"] },
    },
  },
  plugins: [],
} satisfies Config;
