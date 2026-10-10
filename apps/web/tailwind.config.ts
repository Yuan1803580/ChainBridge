import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: "#18201d",
        canvas: "#f4f6f3",
        line: "#dbe1dc",
        brand: {
          50: "#edf8f2",
          100: "#d6efe0",
          500: "#168354",
          600: "#0f6c45",
          700: "#0e5639"
        },
        amber: {
          50: "#fff8e8",
          500: "#bf7923",
          700: "#8a5317"
        }
      },
      boxShadow: {
        panel: "0 1px 2px rgba(24, 32, 29, 0.06)"
      }
    }
  },
  plugins: []
};

export default config;
