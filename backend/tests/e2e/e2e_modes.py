"""HTTP e2e through :3000 for look-alikes, mistake types, final sprint and the weekly mock."""
import json
import os
import time
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
db.execute(text("DELETE FROM weekly_mock_entries"))
db.execute(text("DELETE FROM study_sessions"))
db.commit()

print("Mistake types")
m = db.execute(text("SELECT id, options, correct_option FROM mcqs WHERE id = 186")).first()
wrong = next(k for k, v in m.options.items() if k != m.correct_option and "one-third" in v.lower()) if m else None
if m and wrong:
    st, ans = call(STUDENT, "POST", "/api/study/answer", {"mcq_id": m.id, "selected_option": wrong, "confidence": "sure"})
    check("look-alike wrong answer -> confusion", st == 200 and ans.get("mistake_type") == "confusion", f"({st}, {ans.get('mistake_type')})")
right_other = db.execute(text("SELECT id, options, correct_option FROM mcqs WHERE status = 'ready' AND access = 'open' "
                               "AND id <> 186 ORDER BY id LIMIT 1")).first()   # the student cannot open restricted rows
bad = next(k for k in sorted(right_other.options) if k != right_other.correct_option)
st, ans = call(STUDENT, "POST", "/api/study/answer", {"mcq_id": right_other.id, "selected_option": bad, "confidence": "guess"})
check("wrong guess -> confusion or gap", st == 200 and ans.get("mistake_type") in ("gap", "confusion"), f"({ans.get('mistake_type')})")
st, rd = call(STUDENT, "GET", "/api/study/readiness")
check("readiness has a Paper 1 forecast and standing", st == 200 and {"score", "coverage", "missing"} <= set(rd.get("paper1", {}))
      and "ahead_of" in rd.get("peers", {}), f"({rd.get('paper1', {}).get('score')}, peers={rd.get('peers')})")
check("readiness has mistakes + sprint", st == 200 and "mistakes" in rd and "sprint" in rd,
      f"(types={ {k: v['count'] for k, v in rd.get('mistakes', {}).get('types', {}).items()} }, pairs={rd.get('mistakes', {}).get('pairs')})")

print("Look-alikes")
for _ in range(24):   # the pair is written in the background after the confusion answer
    st, la = call(STUDENT, "GET", "/api/study/lookalikes")
    pairs = la.get("pairs", [])
    if pairs:
        break
    time.sleep(5)
check("lists the student's pairs with questions", st == 200 and pairs and all("questions" in p for p in pairs),
      f"({[(p['term_a'][:30], p['term_b'][:30], p['status'], p['cleared'], len(p['questions'])) for p in pairs]})")

print("Final sprint")
st, sp = call(STUDENT, "GET", "/api/study/sprint")
check("student without a near exam date is locked", st == 200 and not sp.get("unlocked") and not sp.get("preview") and sp.get("items") == [],
      f"(days_left={sp.get('days_left')})")
st, sp = call(ADMIN, "GET", "/api/study/sprint")
items = sp.get("items", [])
check("admin preview builds", st == 200 and sp.get("preview") and items, f"({len(items)} items)")
flash = next((i for i in items if i["type"] == "flash"), None)
check("flash items carry the concept card", flash is not None and flash.get("concept") is not None,
      f"({flash and flash['concept'] and flash['concept'].get('title')})")
q = next((i for i in items if i.get("mcq")), None)
if q:
    st, ans = call(ADMIN, "POST", "/api/study/answer", {"mcq_id": q["mcq_id"], "selected_option": q["mcq"]["correct_option"],
                                                        "confidence": "sure", "dose_index": q["index"], "session_kind": "sprint"})
    check("answer a sprint question", st == 200 and ans.get("is_correct"))
if flash:
    st, _ = call(ADMIN, "POST", f"/api/study/dose/{flash['index']}/done?kind=sprint")
    check("mark a flash card done", st == 200)
st, sp2 = call(ADMIN, "GET", "/api/study/sprint")
done = [i["index"] for i in sp2.get("items", []) if i.get("done")]
check("sprint progress persisted", q and flash and q["index"] in done and flash["index"] in done, f"(done={done})")
st, dose = call(ADMIN, "GET", "/api/study/daily")
check("Daily Dose unaffected by the sprint", st == 200 and "streak" in dose)

print("Weekly mock")
st, ov = call(ADMIN, "GET", "/api/mocks/weekly")
check("overview", st == 200 and ov["entry"]["status"] == "not_started", f"({ov['mock']['title']}, {ov['mock']['total']} q, {ov['mock']['duration_min']} min)")
st, paper = call(ADMIN, "POST", "/api/mocks/weekly/start")
qs = paper.get("questions", [])
check("start: questions without answers", st == 200 and qs and all("correct_option" not in x for x in qs), f"({len(qs)} questions)")
keys = dict(db.execute(text("SELECT id, correct_option FROM mcqs WHERE id = ANY(:ids)"), {"ids": [x["id"] for x in qs]}).all())
answers = {str(x["id"]): {"option": keys[x["id"]], "flagged": i % 10 == 0} for i, x in enumerate(qs[:70])}
st, _ = call(ADMIN, "PUT", "/api/mocks/weekly/progress", {"answers": answers})
st2, resumed = call(ADMIN, "POST", "/api/mocks/weekly/start")
check("autosave + resume", st == 200 and st2 == 200 and len(resumed.get("answers", {})) == 70)
st, res = call(ADMIN, "POST", "/api/mocks/weekly/submit", {"answers": answers})
check("submit scores", st == 200 and res.get("score") == 70, f"({res.get('score')}/{res.get('total')}, passed={res.get('passed')}, rank {res.get('rank')}/{res.get('candidates')})")
st, again = call(ADMIN, "POST", "/api/mocks/weekly/start")
check("second sitting refused (409)", st == 409)
st, res2 = call(ADMIN, "GET", "/api/mocks/weekly/result")
check("result has review with answers + subjects", st == 200 and res2["review"] and "correct_option" in res2["review"][0] and res2["subjects"])
st, _ = call(STUDENT, "POST", "/api/mocks/weekly/start")
st, sres = call(STUDENT, "POST", "/api/mocks/weekly/submit", {"answers": {str(qs[0]["id"]): "A"}})
check("student ranked below admin", st == 200 and sres.get("rank") == 2 and sres.get("candidates") == 2, f"({sres.get('score')}/{sres.get('total')})")
board = sres.get("leaderboard") or []
check("leaderboard: admin first with a masked name, the student's own row named",
      len(board) == 2 and board[0]["rank"] == 1 and "•" in board[0]["name"] and board[1]["you"] and board[1]["name"] == "student",
      f"({[(r['rank'], r['name']) for r in board]})")
st, ov2 = call(STUDENT, "GET", "/api/mocks/weekly")
check("overview history", ov2["entry"]["status"] == "submitted" and ov2["history"] and ov2["candidates"] == 2)
print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
