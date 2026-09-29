"""HTTP e2e through :3000: seeded bank categories, Part 1 / Part 2 weekly papers, pools, explain-on-demand."""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.retention import PAPER1, PAPER2  # noqa: E402  (the seeded banks' category names)

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
db.execute(text("DELETE FROM weekly_mock_entries"))
db.execute(text("DELETE FROM daily_sessions WHERE day = CURRENT_DATE"))
db.commit()
mains = lambda ids: {m for (m,) in db.execute(text("SELECT DISTINCT main_category FROM mcqs WHERE id = ANY(:i)"), {"i": ids})}

print("Seeded bank")
st, stats = call(STUDENT, "GET", "/api/dashboard/stats")
cats = stats.get("categories") or stats.get("categories_map") or {}
check("categories list the seeded parts", st == 200 and PAPER1 in json.dumps(cats, ensure_ascii=False) and PAPER2 in json.dumps(cats, ensure_ascii=False),
      f"({[k for k in cats][:8] if isinstance(cats, dict) else type(cats)})")
check("private categories hidden", "High-yield" not in json.dumps(cats))
st, quiz = call(STUDENT, "POST", "/api/quizzes/start", {"categories": [PAPER2], "sub_categories": ["Radiology"], "num_questions": 10})
qs = quiz.get("questions") or quiz.get("mcqs") or []
check("practice quiz from a seeded specialty", st == 200 and len(qs) == 10, f"({st}, {len(qs)})")

print("Weekly papers")
st, p1 = call(STUDENT, "GET", "/api/mocks/weekly?part=p1")
check("Part 1 paper", st == 200 and p1["mock"]["part"] == "p1" and p1["mock"]["total"] == 100 and p1["mock"]["duration_min"] == 120,
      f"({p1['mock']['title']})")
check("papers list Part 2 specialties", len(p1["papers"][1]["tracks"]) == 8, f"({p1['papers'][1]['tracks']})")
st, p2 = call(STUDENT, "GET", "/api/mocks/weekly?part=p2&track=Medicine")
check("Part 2 Medicine paper", st == 200 and p2["mock"]["track"] == "Medicine" and p2["mock"]["total"] == 100, f"({p2['mock']['title']})")
st, paper = call(STUDENT, "POST", "/api/mocks/weekly/start?part=p2&track=Medicine")
ids = [q["id"] for q in paper.get("questions", [])]
check("Part 2 paper is all Paper 2 Medicine", mains(ids) == {PAPER2} and
      {s for (s,) in db.execute(text("SELECT DISTINCT sub_category FROM mcqs WHERE id = ANY(:i)"), {"i": ids})} == {"Medicine"})
keys = dict(db.execute(text("SELECT id, correct_option FROM mcqs WHERE id = ANY(:i)"), {"i": ids}).all())
st, res = call(STUDENT, "POST", "/api/mocks/weekly/submit?part=p2&track=Medicine",
               {"answers": {str(i): keys[i] for i in ids[:60]}})
check("Part 2 result broken down by topic", st == 200 and res["score"] == 60 and len(res["subjects"]) >= 8,
      f"({[s['subject'] for s in res['subjects']][:6]})")
st, again = call(STUDENT, "GET", "/api/mocks/weekly?part=p1")
check("Part 1 sitting is separate from Part 2", again["entry"]["status"] == "not_started")
st, bogus = call(STUDENT, "GET", "/api/mocks/weekly?part=p2&track=Cardiology")
check("unknown specialty falls back to the mixed Part 2 paper", st == 200 and bogus["mock"]["track"] == "", f"({bogus['mock']['title']})")

print("Pools")
st, dose = call(STUDENT, "GET", "/api/study/daily")
dose_ids = [i["mcq_id"] for i in dose.get("items", []) if i.get("mcq_id")]
check("Daily Dose avoids English / NTS mocks", st == 200 and not (mains(dose_ids) & {"English", "NTS mocks"}), f"({mains(dose_ids)})")
st, duel = call(STUDENT, "POST", "/api/duels", {"subject": "Pathology", "count": 10})
st2, g = call(STUDENT, "GET", f"/api/duels/{duel.get('code')}")
duel_ids = [q["id"] for q in g.get("questions", [])]
check("Pathology duel draws seeded Pathology too", st == 201 and len(duel_ids) == 10 and not (mains(duel_ids) & {"English", "NTS mocks"}),
      f"({mains(duel_ids)})")

print("Explain on demand")
row = db.execute(text("SELECT id, correct_option FROM mcqs WHERE source LIKE 'seed:p1/%' AND explanation_markdown IS NULL LIMIT 1")).first()
st, ans = call(STUDENT, "POST", "/api/study/answer", {"mcq_id": row.id, "selected_option": row.correct_option, "confidence": "sure"})
check("seeded question without explanation answers fine", st == 200 and ans.get("is_correct") and not ans.get("explanation_markdown"))
st, ex = call(STUDENT, "POST", f"/api/mcqs/{row.id}/explain")
check("explain from the textbooks", st == 200 and len(ex.get("answer_markdown") or "") > 80, f"({len(ex.get('answer_markdown') or '')} chars)")
check("explanation cached, still ready", db.execute(text("SELECT status, explanation_markdown IS NOT NULL FROM mcqs WHERE id = :i"), {"i": row.id}).first() == ("ready", True))
print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
