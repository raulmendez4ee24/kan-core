import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          50: "#f5f7fb",
          100: "#e8edf7",
          200: "#cdd9ee",
          300: "#a5b8e0",
          400: "#7f95d2",
          500: "#5f73c2",
          600: "#4c5aa3",
          700: "#3d4782",
          800: "#2f3661",
          900: "#202440"
        }
      }
    }
  },
  plugins: []
};

export default config;

