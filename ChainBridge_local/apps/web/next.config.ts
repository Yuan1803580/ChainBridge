import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  devIndicators: false,
  distDir: process.env.NEXT_DIST_DIR || ".next",
  ...(process.env.NEXT_STANDALONE === "true" ? { output: "standalone" as const } : {}),
};

export default nextConfig;
