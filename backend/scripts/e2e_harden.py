"""HTTP e2e through :3000 for the Mock Builder "harder versions" feature
(app/hardening.py): AI rewrites of existing questions that keep the tested fact and
the correct answer but harden the statement and the options.

The test hardens 5 real bank MCQs, must get back a set where:
  - the set is stored under main_category 'Hardened MCQs' with a quiz_set_id
  - every kept rewrite kept its seed's correct answer (via the mcq_tags 'hardened'
    provenance link) at the requested difficulty
  - the set is visible through the AI-quiz history endpoint
  - validation still rejects bad difficulty / count

The hardened set is deleted at the end, so the account's bank is untouched.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import func, text  # noqa: E402

from app.auth import create_access_token  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import MCQ, MCQTag  # noqa: E402
from app.past_papers import answer_norm, answers_agree  # noqa: E402

BASE = os.environ.get("MEDNAMA_BASE_URL", "http://localhost:3000")
USERNAME = os.environ.get("MEDNAMA_E2E_USER", "student")

GENERATED_CATEGORIES = ("AI MCQs", "Concept re-test", "Past-paper twists", "Look-alikes",
                        "Spot the diagnosis", "High-yield", "Hardened MCQs")

db = SessionLocal()
TOKEN = create_access_token({"sub": USERNAME})


def call(method, path, body=None, timeout=900):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read() or b"{}"
        return e.code, json.loads(raw)


ok = True


def check(name, cond, extra=""):
    global ok
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}", flush=True)
    ok &= bool(cond)


# Pick five real bank questions a student can see (open, ready, embedded).
seeds = (
    db.query(MCQ)
    .filter(MCQ.status == "ready", MCQ.access == "open",
            MCQ.main_category.notin_(GENERATED_CATEGORIES),
            MCQ.stem_embedding.isnot(None))
    .order_by(func.random())
    .limit(5)
    .all()
)
check("found 5 real bank seeds", len(seeds) == 5, f"(got {len(seeds)})")
if not seeds:
    print("No seeds available; give up.", flush=True)
    sys.exit(1)
seed_ids = [s.id for s in seeds]
seed_answer = {s.id: str((s.options or {}).get(s.correct_option, "")) for s in seeds}


def poll(job_id, timeout_s=900):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        time.sleep(4)
        status, body = call("GET", f"/api/chat/harden/jobs/{job_id}")
        if status not in (200, 202):
            return body or {"poll_error": status}
        if body.get("status") == "done":
            return body
        if body.get("status") == "failed":
            return body
    return {"timeout": True}


# ── Validation first (no LLM money burned on bad input) ───────────────────────
st, body = call("POST", "/api/chat/harden/jobs", {"seed_ids": seed_ids, "num_questions": 5, "difficulty": 3})
check("difficulty 3 rejected with 400", st == 400, f"({st}: {body})")
st, body = call("POST", "/api/chat/harden/jobs", {"seed_ids": seed_ids, "num_questions": 7, "difficulty": 4})
check("count 7 rejected with 400", st == 400, f"({st})")
st, body = call("POST", "/api/chat/harden/jobs", {"num_questions": 5, "difficulty": 4})
check("no filters rejected with 400", st == 400, f"({st})")
st, body = call("POST", "/api/chat/harden/jobs", {"seed_ids": [99999999], "num_questions": 5, "difficulty": 4})
check("unknown seeds rejected with 400", st == 400, f"({st}: {body})")
st, body = call("POST", "/api/chat/harden/jobs",
                {"seed_ids": seed_ids, "num_questions": 5, "difficulty": 4, "request_id": "harden-e2e"})
check("valid request starts a job (202)", st == 202, f"({st})")
if st != 202:
    print("Job did not start; aborting.", flush=True)
    db.close()
    sys.exit(1)
job_id = body["job_id"]

res = poll(job_id)
check("job finished done", res.get("status") == "done", f"({res.get('status')}: {res.get('detail') or ''})")

done = False
if res.get("status") == "done" and res.get("result"):
    r = res["result"]
    done = True
    check("set has 1..5 rewrites", 1 <= r.get("total_questions", 0) <= 5,
          f"(total_questions={r.get('total_questions')})")
    check("set difficulty is 4", r.get("difficulty") == 4, f"(got {r.get('difficulty')})")
    check("set title mentions hardened", "Hardened" in (r.get("quiz_set_title") or ""),
          f"({r.get('quiz_set_title')})")

    # Provenance + answer preservation straight from the DB.
    set_id = r["quiz_set_id"]
    kept = db.query(MCQ).filter(MCQ.quiz_set_id == set_id).all()
    check("all stored under 'Hardened MCQs'", kept and all(m.main_category == "Hardened MCQs" for m in kept),
          f"({len(kept)} rows)")
    expected = sum(1 for m in kept if m.difficulty == 4 and m.status == "ready")
    check("records carry difficulty 4 and ready status", expected == len(kept), f"({expected}/{len(kept)})")
    preserved = 0
    for m in kept:
        original = db.query(MCQTag).filter(MCQTag.mcq_id == m.id, MCQTag.axis == "hardened").first()
        if original and original.label.isdigit():
            src = seed_answer.get(int(original.label), "")
            if src and answers_agree(answer_norm(m.options[m.correct_option]), answer_norm(src)):
                preserved += 1
    check("every rewrite kept its seed's answer", preserved == len(kept),
          f"({preserved}/{len(kept)})")

    # History endpoint must now list the hardened set.
    st, hist = call("GET", "/api/chat/ai-quizzes")
    check("history lists the hardened set", st == 200 and any(
        h.get("quiz_set_id") == set_id for h in (hist or [])), f"({st})")

    # Cleanup: delete the hardened set.
    st, _ = call("DELETE", f"/api/chat/ai-quizzes/{set_id}")
    gone = db.query(MCQ).filter(MCQ.quiz_set_id == set_id).count()
    check("set deleted cleanly", st == 204 and gone == 0, f"(delete={st}, left={gone})")

db.close()
print("E2E HARDEN", "PASS" if ok else "FAIL", flush=True)
sys.exit(0 if ok else 1)