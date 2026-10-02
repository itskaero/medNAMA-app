"""HTTP e2e through :3000: the Mastery map (/api/stats/mastery) and the topic-scoped
practice that feeds it.

One topic is practised through the new ``topics`` quiz filter; the map must then show
exactly that many more answers on the (subject, topic) row. The practised attempt is
deleted afterwards, so the account's real stats are untouched.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from app.database import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

BASE = os.environ.get("MEDNAMA_BASE_URL", "http://localhost:3000")
STUDENT = os.environ["MEDNAMA_STUDENT_TOKEN"]


def call(token, method, path, body=None, timeout=600):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw) if raw else None
            except ValueError:
                return r.status, {"raw": raw[:200].decode(errors="replace")}
    except urllib.error.HTTPError as e:
        raw = e.read() or b"{}"
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:200].decode(errors="replace")}


ok = True


def check(name, cond, extra=""):
    global ok
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}", flush=True)
    ok &= bool(cond)


db = SessionLocal()

print("Mastery map loads")
st, m = call(STUDENT, "GET", "/api/stats/mastery")
check("endpoint reachable", st == 200, f"({st})")
check("bank and coverage present", m and m.get("bank", 0) > 0 and "subjects" in m and m.get("pass_line") == 75,
      f"(bank={m.get('bank')}, subjects={len(m.get('subjects', []))})")
check("no archive-bucket subjects", all(s["subject"] not in ("Mixed", "Minor Subjects", "Other") for s in m["subjects"]))

WORTH_IT = 20
subjects = [s for s in m["subjects"] if s["topics"] and any(t["bank"] >= WORTH_IT for t in s["topics"])]
check("enough subject/topic rows to practise", len(subjects) > 0)
subject = next((s for s in subjects if any(t["bank"] >= WORTH_IT for t in s["topics"])), m["subjects"][0])
topic = next(t for t in subject["topics"] if t["bank"] >= WORTH_IT)
before = topic["answered"]
print(f"   practising {subject['subject']} / {topic['topic']} (bank={topic['bank']}, answered before={before})")

print("Topic-scoped practice")
st, quiz = call(STUDENT, "POST", "/api/quizzes/start",
                {"sub_categories": [subject["subject"]], "topics": [topic["topic"]],
                 "num_questions": 5, "prefer_unseen": True, "label": "mastery-e2e"})
check("quiz on that subject+topic starts", st == 200 and len(quiz.get("mcqs", [])) == 5,
      f"({st}, {len(quiz.get('mcqs', []))} mcqs)")
ids = [q["id"] for q in quiz.get("mcqs", [])]
rows = db.execute(text(
    "SELECT id, sub_category, topic FROM mcqs WHERE id = ANY(:ids)"),
    {"ids": ids or [-1]}).fetchall()
check("every question really is that subject+topic",
      bool(rows) and all(r[1] == subject["subject"] and r[2] == topic["topic"] for r in rows),
      f"({[f'{ids[i]}:{r[1]}|{r[2]}' for i, r in enumerate(rows)]})")
if rows and all(r[1] == subject["subject"] and r[2] == topic["topic"] for r in rows):
    attempt = quiz["quiz_attempt_id"]
    st, _ = call(STUDENT, "POST", f"/api/quizzes/{attempt}/submit",
                 {"answers": [{"mcq_id": q["id"], "selected_option": q["correct_option"], "confidence": "sure"}
                              for q in quiz["mcqs"]]})
    check("answers submitted", st == 200, f"({st})")

    st, m2 = call(STUDENT, "GET", "/api/stats/mastery")
    after_subject = next((s for s in m2["subjects"] if s["subject"] == subject["subject"]), None)
    after_topic = next((t for t in (after_subject or {}).get("topics", []) if t["topic"] == topic["topic"]), None)
    check("map counts the new answers", after_topic and after_topic["answered"] == before + 5,
          f"({before} -> {(after_topic or {}).get('answered')})")
    check("map records 100% accuracy", after_topic and after_topic["accuracy"] == 100.0,
          f"({(after_topic or {}).get('accuracy')})")

    db.execute(text("DELETE FROM attempt_answers WHERE quiz_attempt_id = :id"), {"id": attempt})
    db.execute(text("DELETE FROM quiz_attempts WHERE id = :id"), {"id": attempt})
    db.execute(text("DELETE FROM answer_events WHERE session_ref = :ref"), {"ref": f"quiz:{attempt}"})
    db.commit()

db.close()
print("MASTERY E2E:", "ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)