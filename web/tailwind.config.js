/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    container: { center: true, padding: "2rem", screens: { "2xl": "1400px" } },
    extend: {
      fontFamily: {
        // Editorial sans for body
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        // Source Serif Pro for memos / lessons / hero copy
        serif: ['"Source Serif 4"', '"Source Serif Pro"', "Georgia", "serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        subtle: "hsl(var(--subtle))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        warning: {
          DEFAULT: "hsl(var(--warning))",
          foreground: "hsl(var(--warning-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // Ocean palette — pulled from orcaqubits-ai.com
        ocean: {
          50:  "#e8f4fc",
          100: "#d1e9f9",
          200: "#a3d3f3",
          300: "#5fb8eb",
          400: "#2ba3e4",
          500: "#0090db",
          600: "#0077b8",
          700: "#005f94",
          800: "#1a3a52",
          900: "#152a3d",
        },
        // Per-agent identity hues
        agent: {
          research: "hsl(var(--agent-research))",
          content: "hsl(var(--agent-content))",
          review: "hsl(var(--agent-review))",
          analytics: "hsl(var(--agent-analytics))",
          cmo: "hsl(var(--agent-cmo))",
          positioning: "hsl(var(--agent-positioning))",
          "customer-voice": "hsl(var(--agent-customer-voice))",
          "lifecycle-email": "hsl(var(--agent-lifecycle-email))",
          "paid-media": "hsl(var(--agent-paid-media))",
          "ops-qa": "hsl(var(--agent-ops-qa))",
          "self-critique": "hsl(var(--agent-self-critique))",
          "image-brief": "hsl(var(--agent-image-brief))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
};
