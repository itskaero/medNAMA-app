"""HTTP e2e through :3000 for Past Papers: access gate, filters, practice via the MCQ pool, timed papers."""
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
restricted = set(db.execute(text("SELECT id FROM mcqs WHERE access = 'restricted'")).scalars().all())
print(f"{len(restricted)} restricted (past-paper) questions")

print("Admin")
st, ov = call(ADMIN, "GET", "/api/past-papers")
exams = ov.get("exams", [])
fcps = next((e for e in exams if e["exam"] == "FCPS Part 1"), None)
yrs = [y["year"] for y in (fcps or {}).get("years", [])]
check("overview: FCPS Part 1 years 2026..2016 then collections", st == 200 and not ov.get("locked") and fcps
      and yrs[:5] == [2026, 2025, 2024, 2023, 2022] and 2016 in yrs and yrs[-1] is None,
      f"({yrs})")
y24 = next(y for y in fcps["years"] if y["year"] == 2024)
check("a year counts distinct questions, not the sum of its sittings",
      y24["total"] <= sum(p["total"] for p in y24["papers"]) and len(y24["papers"]) > 1,
      f"({y24['total']} vs {sum(p['total'] for p in y24['papers'])} over {len(y24['papers'])} sittings)")
check("Dentistry is its own exam", any(e["exam"] == "FCPS Part 1 (Dentistry)" for e in exams),
      f"({[e['exam'] for e in exams]})")
st, sc = call(ADMIN, "POST", "/api/past-papers/scope", {"exam": "FCPS Part 1", "years": [2024]})
check("scope for 2024 (both archives; standard subjects)", st == 200 and sc["count"] > 2455 and len(sc["facets"]["subject"]) >= 8
      and {"Radiant", "MediVerse"} <= {f["label"] for f in sc["facets"]["source"]} <= {"Radiant", "MediVerse", "Question bank"},
      f"({sc.get('count')}, subjects={[f['label'] for f in sc.get('facets', {}).get('subject', [])]})")
st, sc2 = call(ADMIN, "POST", "/api/past-papers/scope",
               {"exam": "FCPS Part 1", "years": [2024, 2025], "tags": {"subject": ["Physiology"], "topic": ["Renal"]}})
check("subject + topic + two years", st == 200 and 0 < sc2["count"] < sc["count"] * 2, f"({sc2.get('count')})")
phys_topics = {f["label"] for f in sc2["facets"]["topic"]}
check("topic facet ignores its own selection (can widen)", len(phys_topics) > 1, f"({len(phys_topics)} topics)")

st, quiz = call(ADMIN, "POST", "/api/quizzes/start", {"past_paper_exam": "FCPS Part 1", "years": [2024],
                                                      "tags": {"subject": ["Physiology"]}, "num_questions": 10})
qs = quiz.get("mcqs") or []
ids = [q["id"] for q in qs]
subj_ok = db.execute(text("SELECT count(*) FROM mcqs WHERE sub_category='Physiology' AND id = ANY(:i)"),
                     {"i": ids}).scalar() == len(ids)
check("practice quiz from the MCQ pool honours filters", st == 200 and len(qs) == 10 and subj_ok
      and all(2024 in q.get("paper_years", []) for q in qs), f"({st}, years of first: {qs[0].get('paper_years') if qs else None})")

st, tp = call(ADMIN, "POST", "/api/past-papers/timed", {"exam": "FCPS Part 1", "years": [2025], "count": 100, "minutes": 120})
check("timed paper created", st == 201 and tp["total"] == 100 and tp["duration_min"] == 120, f"({tp.get('title')})")
mock_id = tp.get("mock_id")
st, paper = call(ADMIN, "POST", f"/api/mocks/{mock_id}/start")
pids = [q["id"] for q in paper.get("questions", [])]
subjects = db.execute(text("SELECT sub_category, count(*) FROM mcqs WHERE id = ANY(:i) GROUP BY 1"),
                      {"i": pids}).all()
groups = db.execute(text("SELECT count(*) - count(DISTINCT coalesce(recall_group, -id)) FROM mcqs WHERE id = ANY(:i)"), {"i": pids}).scalar()
check("timed paper: one version per question", groups == 0, f"({groups} repeats)")
check("timed paper mixes all subjects, no answers sent", st == 200 and len(pids) == 100 and len(subjects) >= 8
      and all("correct_option" not in q for q in paper["questions"]), f"({dict(subjects)})")
keys = dict(db.execute(text("SELECT id, correct_option FROM mcqs WHERE id = ANY(:i)"), {"i": pids}).all())
st, res = call(ADMIN, "POST", f"/api/mocks/{mock_id}/submit", {"answers": {str(i): keys[i] for i in pids[:80]}})
check("timed paper scored", st == 200 and res["score"] == 80 and res["passed"], f"({res.get('score')}/{res.get('total')})")
st, _ = call(STUDENT, "GET", f"/api/mocks/{mock_id}/result")
check("another user cannot open your timed paper", st == 404, f"({st})")
st, wk = call(ADMIN, "GET", "/api/mocks/weekly?part=p1")
check("timed papers stay out of weekly history", st == 200 and all(h.get("part") in ("p1", "p2") for h in wk.get("history", [])))

print("Student (PAST_PAPERS_ACCESS=admin)")
st, ov = call(STUDENT, "GET", "/api/past-papers")
check("past papers locked", st == 200 and ov.get("locked") is True)
st, _ = call(STUDENT, "POST", "/api/past-papers/scope", {"exam": "FCPS Part 1"})
check("scope refused", st == 403, f"({st})")
st, quiz = call(STUDENT, "POST", "/api/quizzes/start", {"past_paper_exam": "FCPS Part 1", "num_questions": 10})
check("practice quiz cannot reach restricted questions", st in (400, 404) or not ({q["id"] for q in quiz.get("mcqs", [])} & restricted), f"({st})")
st, quiz = call(STUDENT, "POST", "/api/quizzes/start", {"categories": ["Past papers · FCPS Part 1"], "num_questions": 10})
check("category route blocked too", st in (400, 404) or not ({q["id"] for q in quiz.get("mcqs", [])} & restricted), f"({st})")
rid = next(iter(restricted))
st, _ = call(STUDENT, "POST", "/api/study/answer", {"mcq_id": rid, "selected_option": "A", "confidence": "sure"})
check("answering a restricted id -> 404", st == 404, f"({st})")
st, stats = call(STUDENT, "GET", "/api/dashboard/stats")
check("dashboard categories hide past papers", "Past papers" not in json.dumps(stats.get("categories")))

print("Shared features stay open-only")
st, duel = call(ADMIN, "POST", "/api/duels", {"subject": None, "count": 10})
st2, g = call(ADMIN, "GET", f"/api/duels/{duel.get('code')}")
check("admin duel has no restricted questions", st == 201 and not ({q["id"] for q in g.get("questions", [])} & restricted))
wk_ids = set(db.execute(text("SELECT jsonb_array_elements_text(mcq_ids)::int FROM weekly_mocks WHERE part IN ('p1','p2')")).scalars().all())
check("weekly mock papers have no restricted questions", not (wk_ids & restricted), f"({len(wk_ids)} checked)")
print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
