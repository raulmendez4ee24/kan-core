import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ivory: "#f5f3ef",
        sand: "#e7e2d9",
        graphite: "#1c1f24",
        ink: "#0f1217",
        accent: "#294d73",
      },
      boxShadow: {
        soft: "0 10px 30px rgba(14, 20, 29, 0.12)",
      },
    },
  },
  plugins: [],
} satisfies Config;
