FROM node:22-bookworm-slim AS build

WORKDIR /src/frontend

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Local source (build context = repo root)
COPY frontend/ /src/frontend/

# Make browser API calls relative to the current host (same-origin /api).
# This turns `http://localhost:8000` into `""` inside src/lib/constants.ts.
RUN sed -i 's#http://localhost:8000##g' src/lib/constants.ts

# Reverse-proxy /api/* to the backend service inside the compose network.
RUN cat > next.config.ts <<'EOF'
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",

  // The rewrite proxy drops requests after 30 s by default, which cut off
  // AI chat answers and quiz generation mid-flight ("Failed to fetch").
  experimental: {
    proxyTimeout: 180_000,
  },

  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://backend:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
EOF

RUN npm install && npm run build

FROM node:22-bookworm-slim AS runtime

ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

WORKDIR /app

COPY --from=build /src/frontend/.next/standalone ./
COPY --from=build /src/frontend/.next/static ./.next/static
COPY --from=build /src/frontend/public ./public

EXPOSE 3000

CMD ["node", "server.js"]