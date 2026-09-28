#!/usr/bin/env bash
# End-to-end suites: HTTP through the running site (frontend proxy -> backend -> DB), as admin and a student.
#
#   bash backend/tests/e2e/run.sh                 # every suite (some call the AI: summaries, twists, referee)
#   bash backend/tests/e2e/run.sh --no-llm        # skip the checks that call the AI where a suite supports it
#   bash backend/tests/e2e/run.sh past_papers modes   # only these suites (e2e_<name>.py)
#   bash backend/tests/e2e/run.sh --reset         # afterwards, delete the answers/sessions the suites created
#
# Needs the local stack (docker compose ... up) with the dev DB on :5434 (backend/.env), and Python with the
# backend requirements. Tokens are minted inside the backend container so they match its secret. The suites
# write answers, quizzes, duels and a 'student' account: use --reset (scripts/reset_user_data.py --apply)
# before handing the database to anyone.
set -uo pipefail
cd "$(dirname "$0")"
BACKEND="$(cd ../.. && pwd)"
CONTAINER="${MEDNAMA_CONTAINER:-mednama-backend}"
export MEDNAMA_BASE_URL="${MEDNAMA_BASE_URL:-http://localhost:3000}"
export PYTHONIOENCODING=utf-8

RESET=0; ARGS=(); SUITES=()
for a in "$@"; do
  case "$a" in
    --reset) RESET=1 ;;
    --*) ARGS+=("$a") ;;
    *) SUITES+=("e2e_$a.py") ;;
  esac
done
[ ${#SUITES[@]} -eq 0 ] && SUITES=(e2e_*.py)

(cd "$BACKEND" && python scripts/seed_users.py >/dev/null)   # the student the access checks log in as
mint() {
  MSYS_NO_PATHCONV=1 docker exec -w /app/backend "$CONTAINER" python -c \
    "from app.auth import create_access_token; from datetime import timedelta; print(create_access_token({'sub': '$1'}, timedelta(hours=6)))" \
    2>/dev/null | tail -1
}
export MEDNAMA_ADMIN_TOKEN="$(mint admin)" MEDNAMA_STUDENT_TOKEN="$(mint student)"
[ -n "$MEDNAMA_ADMIN_TOKEN" ] || { echo "Could not mint tokens in container $CONTAINER (is the stack up?)"; exit 1; }

failed=()
for s in "${SUITES[@]}"; do
  echo "===== $s"
  if (cd "$BACKEND" && python "tests/e2e/$s" "${ARGS[@]}"); then :; else failed+=("$s"); fi
done

if [ "$RESET" = 1 ]; then (cd "$BACKEND" && python scripts/reset_user_data.py --apply | tail -3); fi
echo
if [ ${#failed[@]} -eq 0 ]; then echo "ALL SUITES PASSED (${#SUITES[@]})"; else echo "FAILED: ${failed[*]}"; exit 1; fi
