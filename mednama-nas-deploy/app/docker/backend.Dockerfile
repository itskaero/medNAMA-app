FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface \
    TRANSFORMERS_CACHE=/models/huggingface

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential curl ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (leverages build cache)
COPY requirements.txt /app/backend/requirements.txt
# The app quantizes models to INT8 and runs them on CPU. Pin the EXACT torch /
# torchvision pairing that is verified to work with this codebase on the dev
# machine (torch 2.13.0 <-> torchvision 0.28.0). The latest pair (2.14/0.29)
# crashes with `torchvision::nms does not exist` (pip does not enforce the
# torch<->torchvision binary pairing). Also pin the transformers /
# sentence-transformers set that works on the dev machine instead of chasing
# breaking latest releases.
RUN pip install --upgrade pip \
    && pip install torch==2.13.0 torchvision==0.28.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install transformers==5.13.1 sentence-transformers==5.6.0 \
    && pip install -r /app/backend/requirements.txt

# Copy the application source (build context = repo root)
COPY backend/ /app/backend/
COPY docker/backend-entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh && mkdir -p /models/huggingface /tmp /app/mcqs

WORKDIR /app/backend
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]