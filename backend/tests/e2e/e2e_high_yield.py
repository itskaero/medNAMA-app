"""High-yield Daily Dose: admins get recall-weighted questions; students never see private ones."""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

BASE = os.environ.get("MEDNAMA_BASE_URL", "http://localhost:3000")
ADMIN = os.environ["MEDNAMA_ADMIN_TOKEN"]
STUDENT = os.environ["MEDNAMA_STUDENT_TOKEN"]


def call(token, method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read() or b"{}"
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body[:200].decode(errors="replace")}


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")
    return bool(cond)


db = SessionLocal()
private_ids = set(db.execute(text("SELECT id FROM mcqs WHERE status = 'private'")).scalars().all())
# Rebuild today's Dose for both accounts so the new picker runs.
db.execute(text("DELETE FROM daily_sessions WHERE day = CURRENT_DATE AND user_id IN (1, 2)"))
db.commit()
print(f"{len(private_ids)} private high-yield questions in the bank")

ok = True
print("Admin Daily Dose")
st, dose = call(ADMIN, "GET", "/api/study/daily")
hy = [i for i in dose.get("items", []) if i.get("high_yield")]
ok &= check("dose builds", st == 200, f"({st}, types={[i['type'] for i in dose.get('items', [])]})")
ok &= check("contains high-yield questions", len(hy) >= 1,
            f"({len(hy)}: {[(i.get('times_asked'), (i.get('mcq') or {}).get('question_text', '')[:50]) for i in hy]})")
if hy:
    item = hy[0]
    st, ans = call(ADMIN, "POST", "/api/study/answer", {"mcq_id": item["mcq_id"], "selected_option": item["mcq"]["correct_option"],
                                                       "confidence": "sure", "dose_index": item["index"]})
    ok &= check("admin can answer a high-yield question", st == 200 and ans.get("is_correct") is True, f"({st})")

print("Student")
st, sdose = call(STUDENT, "GET", "/api/study/daily")
leaked = [i for i in sdose.get("items", []) if i.get("high_yield") or i.get("mcq_id") in private_ids]
ok &= check("student dose builds without private questions", st == 200 and not leaked, f"({st}, leaked={len(leaked)})")
pid = next(iter(private_ids), None)
if pid:
    st, _ = call(STUDENT, "POST", "/api/study/answer", {"mcq_id": pid, "selected_option": "A", "confidence": "sure"})
    ok &= check("student answering a private id -> 404", st == 404, f"({st})")
    st, _ = call(STUDENT, "POST", f"/api/mcqs/{pid}/explain")
    ok &= check("student explaining a private id -> 404", st == 404, f"({st})")
    st, _ = call(STUDENT, "POST", f"/api/bookmarks/mcq/{pid}")
    ok &= check("student bookmarking a private id -> 404", st == 404, f"({st})")
st, lst = call(STUDENT, "GET", "/api/mcqs?category=High-yield")
ok &= check("MCQ browser hides private questions", st == 200 and not any(m["id"] in private_ids for m in lst), f"({st}, {len(lst)} listed)")
st, quiz = call(STUDENT, "POST", "/api/quizzes/start", {"categories": ["High-yield"], "num_questions": 20})
qids = [q.get("id") for q in (quiz.get("questions") or quiz.get("mcqs") or [])] if isinstance(quiz, dict) else []
ok &= check("quiz start never serves private questions", not any(i in private_ids for i in qids), f"({st}, {len(qids)} served)")
st_after = db.execute(text("SELECT count(*) FROM mcqs WHERE status = 'private'")).scalar()
ok &= check("private questions stay private", st_after >= len(private_ids), f"({st_after})")
print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
