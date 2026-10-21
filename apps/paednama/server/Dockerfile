# PaedNAMA's API on the NAS: medNAMA's backend image with the PaedNAMA code added. One process serves medNAMA's
# API and /api/paeds, so the models load once (the NAS has 3.8 GB). No pip install: PaedNAMA needs nothing new.
# Build on the NAS, next to the image it extends (context = a folder holding apps/):
#   docker build -f apps/paednama/server/Dockerfile -t app-backend-paeds:latest .
ARG BASE=app-backend:latest
FROM ${BASE}

COPY apps/ /app/apps/
COPY apps/paednama/server/entrypoint.sh /paednama-entrypoint.sh
RUN chmod +x /paednama-entrypoint.sh

ENTRYPOINT ["/paednama-entrypoint.sh"]
