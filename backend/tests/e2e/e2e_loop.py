"""E2E through :3000 for the daily-loop features (admin token)."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("MEDNAMA_BASE_URL", "http://localhost:3000")
TOKEN = os.environ["MEDNAMA_ADMIN_TOKEN"]
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def call(method, path, body=None, timeout=300):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=H, method=method)
    t = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r), time.monotonic() - t
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), time.monotonic() - t


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")
    return cond


ok = True
print("Daily Dose")
st, dose, dt = call("GET", "/api/study/daily")
ok &= check("GET /api/study/daily", st == 200 and "items" in dose, f"({st}, {len(dose.get('items', []))} items, {dt:.1f}s, types={[i['type'] for i in dose.get('items', [])]})")
mcq_items = [i for i in dose.get("items", []) if i.get("mcq") and not i.get("done")]
if mcq_items:
    item = mcq_items[0]
    wrong = next(k for k in sorted(item["mcq"]["options"]) if k != item["mcq"]["correct_option"])
    st, ans, dt = call("POST", "/api/study/answer", {"mcq_id": item["mcq"]["id"], "selected_option": wrong, "confidence": "sure", "dose_index": item["index"]})
    ok &= check("POST /api/study/answer (wrong)", st == 200 and ans.get("is_correct") is False, f"({st}, concept_status={ans.get('concept_status')}, {dt:.1f}s)")
    concept = ans.get("concept")
    t0 = time.monotonic()
    while concept is None and time.monotonic() - t0 < 240:
        time.sleep(4)
        st, body, _ = call("GET", f"/api/concepts/by-mcq/{item['mcq']['id']}")
        if st == 200:
            concept = body["concept"]
    ok &= check("concept card ready (poll)", concept is not None,
                f"after {time.monotonic() - t0:.0f}s: {concept and concept['title']!r} [{concept and concept['grounding']}] {concept and concept['book_title']} p.{concept and concept['page_number']}")
    if concept:
        st, ex, dt = call("POST", f"/api/concepts/{concept['id']}/explain-back", {"explanation": concept["summary"][:300]})
        ok &= check("POST explain-back", st == 200 and isinstance(ex.get("score"), int), f"({st}, score={ex.get('score')}, {dt:.1f}s)")
print("Readiness / exam date")
st, body, _ = call("PUT", "/api/study/exam-date", {"exam_date": "2026-12-15"})
ok &= check("PUT exam-date", st == 200 and body.get("exam_date") == "2026-12-15")
st, rd, _ = call("GET", "/api/study/readiness")
ok &= check("GET readiness", st == 200 and "concepts" in rd, f"(days_left={rd.get('days_left')}, target={rd.get('daily_target')}, concepts={rd.get('concepts')}, streak={rd.get('streak')})")
print("Duel")
st, d, _ = call("POST", "/api/duels", {"subject": None, "count": 10})
ok &= check("POST /api/duels", st == 201 and bool(d.get("code")), f"({st}, {d})")
if d.get("code"):
    st, g, _ = call("GET", f"/api/duels/{d['code']}")
    hidden = all("correct_option" not in q for q in g.get("questions", []))
    ok &= check("GET duel hides answers before playing", st == 200 and hidden and g.get("total") == 10)
    answers = {str(q["id"]): sorted(q["options"])[0] for q in g["questions"]}
    st, res, _ = call("POST", f"/api/duels/{d['code']}/submit", {"answers": answers, "time_ms": 91000})
    shown = all("correct_option" in q for q in res.get("questions", []))
    ok &= check("POST duel submit -> results with answers", st == 200 and bool(res.get("played")) and shown, f"(score {res.get('players', [{}])[0].get('score')}/10)")
print("Referee (admin)")
st, rf, dt = call("POST", "/api/referee", {"question": "Pericardial cavity lies between", "answer": "Visceral and parietal layer of fibrous pericardium"})
ok &= check("POST /api/referee", st == 200 and bool(rf.get("verdict")) and rf.get("verdict") in ("contradicted", "supported", "books_conflict", "textbooks_silent"),
            f"({st}, verdict={rf.get('verdict')}, quotes={len(rf.get('evidence', []))}, {dt:.1f}s)")
st, rc, _ = call("GET", "/api/recalls?verdict=unrefereed&limit=3")
ok &= check("GET /api/recalls", st == 200 and "items" in rc, f"(total unrefereed={rc.get('total')}, counts={rc.get('counts')})")
print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
