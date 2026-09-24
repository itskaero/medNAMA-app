#!/usr/bin/env bash
# Build the medNAMA images on this PC and package them for the NAS.
#
#   bash scripts/make_nas_bundle.sh                 # checks + build + save
#   bash scripts/make_nas_bundle.sh --with-models   # also bundle the MedCPT reranker
#                                                   # (NAS then needs no internet for it)
#   bash scripts/make_nas_bundle.sh --skip-checks   # skip tsc / py_compile
#
# Output: mednama-nas-deploy/ with mednama-backend.tar, mednama-frontend.tar,
# SHA256SUMS, MANIFEST.txt, deploy.sh, rollback.sh, restore_nas.sh, app/.
# Copy that folder to the NAS and run:  bash deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/mednama-nas-deploy"
WITH_MODELS=0
SKIP_CHECKS=0
for arg in "$@"; do
  case "$arg" in
    --with-models) WITH_MODELS=1 ;;
    --skip-checks) SKIP_CHECKS=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

step() { echo; echo "==> $*"; }

command -v docker >/dev/null || { echo "ERROR: docker not found"; exit 1; }
docker info >/dev/null 2>&1 || { echo "ERROR: Docker is not running (start Docker Desktop)"; exit 1; }
[ -f .env ] || { echo "ERROR: $ROOT/.env missing (compose needs it for the build)"; exit 1; }

PY=python; command -v python >/dev/null || PY=python3
if [ "$SKIP_CHECKS" = 0 ]; then
  step "Checks: backend compiles, frontend type-checks"
  "$PY" -m py_compile backend/app/*.py backend/scripts/*.py
  (cd frontend && npx tsc --noEmit -p .)
fi

DIRTY="$(git status --porcelain -- backend frontend docker NAS 2>/dev/null | head -5 || true)"
if [ -n "$DIRTY" ]; then
  echo
  echo "WARNING: uncommitted changes will be baked into the images:"
  echo "$DIRTY"
fi

step "Building images (nas-backend, nas-frontend)"
docker compose --env-file ./.env -f NAS/docker-compose.yaml build

step "Verifying the backend image with its own Python (compile + import)"
# The image runs Python 3.11, which is stricter than a newer local Python
# (e.g. backslashes inside f-string expressions). Never ship an image whose
# code doesn't import - it would crash-loop on the NAS.
# MSYS_NO_PATHCONV: stop Git Bash on Windows rewriting "/app/backend" into a Windows path.
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint python -e DATABASE_URL=postgresql://x:x@127.0.0.1:1/x -w /app/backend \
  nas-backend:latest -c "import compileall, sys
ok = compileall.compile_dir('app', quiet=1) and compileall.compile_dir('scripts', quiet=1) \
     and compileall.compile_dir('alembic', quiet=1)
import app.main, app.quiz_generation, app.retrieval
print('image code OK on Python', sys.version.split()[0])
sys.exit(0 if ok else 1)"

mkdir -p "$OUT/app/docker"
step "Saving images into $OUT (several minutes, ~0.9 GB)"
docker save nas-backend:latest -o "$OUT/mednama-backend.tar"
docker save nas-frontend:latest -o "$OUT/mednama-frontend.tar"

step "Copying deploy files"
cp docker/backend-entrypoint.sh "$OUT/app/docker/backend-entrypoint.sh"
cp docker/backend.Dockerfile "$OUT/app/docker/backend.Dockerfile"
cp backend/scripts/restore_nas.sh "$OUT/restore_nas.sh"
if [ ! -f "$OUT/app/.env" ]; then
  echo "NOTE: $OUT/app/.env not present - deploy.sh will keep the NAS's existing .env."
fi

rm -f "$OUT/hf-medcpt.tar"
if [ "$WITH_MODELS" = 1 ]; then
  HF_HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub"
  MODEL_DIR="models--ncbi--MedCPT-Cross-Encoder"
  if [ -d "$HF_HUB/$MODEL_DIR" ]; then
    step "Bundling MedCPT reranker from $HF_HUB"
    tar -cf "$OUT/hf-medcpt.tar" -C "$HF_HUB" "$MODEL_DIR"
  else
    echo "WARNING: $HF_HUB/$MODEL_DIR not found - the NAS will download MedCPT on first start."
  fi
fi

step "Writing SHA256SUMS and MANIFEST.txt"
(cd "$OUT" && sha256sum ./*.tar > SHA256SUMS)
ALEMBIC_HEAD="$("$PY" - <<'PYEOF'
import glob, re
revs, downs = {}, set()
for path in glob.glob("backend/alembic/versions/*.py"):
    src = open(path, encoding="utf-8").read()
    rev = re.search(r"^revision: str = '([^']+)'", src, re.M)
    down = re.search(r"^down_revision: [^=]+= '([^']+)'", src, re.M)
    if rev:
        revs[rev.group(1)] = path
    if down:
        downs.add(down.group(1))
print(" ".join(sorted(set(revs) - downs)))
PYEOF
)"
{
  echo "medNAMA NAS bundle"
  echo "built:          $(date '+%Y-%m-%d %H:%M')"
  echo "git branch:     $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  echo "git commit:     $(git rev-parse --short HEAD 2>/dev/null || echo '?')$( [ -n "$DIRTY" ] && echo ' (+ uncommitted changes)')"
  echo "backend image:  $(docker image inspect nas-backend:latest --format '{{.Id}}' | cut -c8-19)"
  echo "frontend image: $(docker image inspect nas-frontend:latest --format '{{.Id}}' | cut -c8-19)"
  echo "alembic head:   ${ALEMBIC_HEAD:-?} (applied automatically when the backend starts)"
  echo "MedCPT bundled: $( [ -f "$OUT/hf-medcpt.tar" ] && echo yes || echo 'no (downloaded on first start)')"
  echo
  echo "Optional .env settings (defaults shown):"
  echo "  RERANKER_SECOND_STAGE=ncbi/MedCPT-Cross-Encoder   (empty = single-stage, faster on a weak CPU)"
  echo "  QUERY_REWRITE=true   LLM_THINKING=false   LOG_LEVEL=INFO"
  echo "  LLM_CHAT_MODEL / LLM_CHAT_BASE_URL / LLM_CHAT_API_KEY   (answer model override)"
  echo "  LLM_FAST_MODEL / LLM_FAST_BASE_URL / LLM_FAST_API_KEY   (rewrite + MCQ model override)"
} > "$OUT/MANIFEST.txt"
cat "$OUT/MANIFEST.txt"

echo
echo "DONE. Copy the folder to the NAS, then on the NAS (as root, inside the folder):"
echo "    bash deploy.sh                 # first time after this update: bash deploy.sh --data-fixes"
du -sh "$OUT" 2>/dev/null | awk '{print "Bundle size: " $1}'
