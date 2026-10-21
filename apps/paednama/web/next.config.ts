import path from "path";
import type { NextConfig } from "next";

// PaedNAMA's server (apps/paednama/server) serves medNAMA's API plus /api/paeds on one port.
const BACKEND_URL = process.env.PAEDNAMA_API_URL || "http://127.0.0.1:8780";

const REPO = path.resolve(__dirname, "../../..");

const nextConfig: NextConfig = {
  // The app imports medNAMA's UI (frontend/src) read-only, so the build root is the repository.
  turbopack: { root: REPO },
  // The Docker image (apps/paednama/web/Dockerfile) builds a standalone server; local dev and `next start` don't.
  ...(process.env.PAEDNAMA_STANDALONE ? { output: "standalone" as const, outputFileTracingRoot: REPO } : {}),
  experimental: { proxyTimeout: 180_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
