# PaedNAMA web image (build context = repo root):
#   docker build -f apps/paednama/web/Dockerfile -t paednama-web:latest .
# The app imports medNAMA's UI (frontend/src) read-only, so both folders go into the build. /api is proxied to the
# backend service on the compose network (backend:8000), which serves medNAMA's API plus /api/paeds.
FROM node:22-bookworm-slim AS build

WORKDIR /src
COPY frontend/ /src/frontend/
COPY apps/paednama/web/ /src/apps/paednama/web/

RUN cd frontend && npm install
# one copy of React and Next for both apps, as on the PC (scripts/link_modules.bat)
RUN ln -s ../../../frontend/node_modules /src/apps/paednama/web/node_modules   # relative, so it still resolves in the runtime image
# medNAMA's fonts, which the shared stylesheets ask for (/fonts/...)
RUN mkdir -p apps/paednama/web/public && cp -r frontend/public/fonts apps/paednama/web/public/fonts

ARG MEDNAMA_URL=https://mednama.rekorex.online
ENV PAEDNAMA_API_URL=http://backend:8000 PAEDNAMA_STANDALONE=1 NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_MEDNAMA_URL=${MEDNAMA_URL}
RUN cd apps/paednama/web && npx next build

FROM node:22-bookworm-slim AS runtime

ENV NODE_ENV=production PORT=3100 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

# standalone output is traced from the repo root, so the server sits at apps/paednama/web/server.js
COPY --from=build /src/apps/paednama/web/.next/standalone ./
COPY --from=build /src/apps/paednama/web/.next/static ./apps/paednama/web/.next/static
COPY --from=build /src/apps/paednama/web/public ./apps/paednama/web/public

EXPOSE 3100
CMD ["node", "apps/paednama/web/server.js"]
