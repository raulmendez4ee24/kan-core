import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#06080f",
        cyan: "#42e8ff",
        neon: "#9f5bff"
      }
    }
  },
  plugins: []
} satisfies Config;
