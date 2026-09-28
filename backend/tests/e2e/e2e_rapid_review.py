"""HTTP e2e through :3000: Rapid Review keys + summary, and 'work through all' batching."""
import json
import os
import re
import sys
import time
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
scope = {"exam": "FCPS Part 1", "tags": {"topic": ["Renal"]}}

print("Answer keys")
st, k = call(ADMIN, "POST", "/api/study/keys", {**scope, "limit": 100})
items = k.get("items", [])
yrs = [len(i["years"]) for i in items]
check("keys for Renal, most asked first", st == 200 and k["total"] > 400 and yrs == sorted(yrs, reverse=True) and items[0]["answer"],
      f"({k.get('total')} total; first asked {yrs[:5]}; '{items[0]['question_text'][:60]}' -> {items[0]['answer'][:40]})")
st, k2 = call(ADMIN, "POST", "/api/study/keys", {**scope, "offset": 100, "limit": 100})
check("paging", st == 200 and not ({i["id"] for i in items} & {i["id"] for i in k2["items"]}))
st, km = call(ADMIN, "POST", "/api/study/keys", {**scope, "only_missed": True})
check("missed-only works", st == 200 and km["total"] <= k["total"], f"({km.get('total')})")
st, kb = call(ADMIN, "POST", "/api/study/keys", {"main": "Paper 1 · Basic sciences", "sub": "Physiology", "limit": 20})
check("bank category keys", st == 200 and kb["total"] > 500, f"({kb.get('total')})")
st, ks = call(STUDENT, "POST", "/api/study/keys", {**scope, "limit": 200})
restricted = set(db.execute(text("SELECT id FROM mcqs WHERE access='restricted'")).scalars().all())
check("student gets no restricted keys", st == 200 and not ({i["id"] for i in ks.get("items", [])} & restricted), f"({ks.get('total')} open)")

print("One-page summary")
db.execute(text("DELETE FROM topic_summaries"))
db.commit()
st, _ = call(ADMIN, "POST", "/api/study/topic-summary", {"exam": "FCPS Part 1"})
check("needs a topic", st == 400, f"({st})")
t0 = time.time()
st, s1 = call(ADMIN, "POST", "/api/study/topic-summary", scope)
dt1 = time.time() - t0
md = s1.get("markdown", "")
refs = re.findall(r"\[([^\[\]\n]{2,80}?),\s*(?:Page|p\.)\s*(\d{1,4})\]", md, re.I)
check("summary written", st == 200 and not s1.get("cached") and "## " in md and len(md) > 600,
      f"({dt1:.0f}s, {len(md)} chars, {len(s1.get('citations', []))} refs, {s1.get('key_count')} keys)")
check("every inline reference is a checked citation", all((t.strip(), int(p)) in {(c['book_title'], c['page_number']) for c in s1.get('citations', [])} for t, p in refs),
      f"({len(refs)} inline)")
print("   ---- summary preview ----")
for line in md.splitlines()[:18]:
    print("   " + line[:150])
t0 = time.time()
st, s2 = call(ADMIN, "POST", "/api/study/topic-summary", {**scope, "years": [2024]})
check("second call served from cache (years ignored)", st == 200 and s2.get("cached") and time.time() - t0 < 5, f"({time.time() - t0:.1f}s)")
st, _ = call(STUDENT, "POST", "/api/study/topic-summary", scope)
check("student cannot read the restricted summary", st == 403, f"({st})")

print("Work through all (batches)")
f = {"past_paper_exam": "FCPS Part 1", "years": [2022], "tags": {"topic": ["Renal"]}, "num_questions": 20, "prefer_unseen": True}
st, q1 = call(ADMIN, "POST", "/api/quizzes/start", f)
check("scope counts returned", st == 200 and q1.get("total_in_scope", 0) > 20 and q1.get("unseen_in_scope") is not None,
      f"(total {q1.get('total_in_scope')}, unseen {q1.get('unseen_in_scope')})")
ids1 = [m["id"] for m in q1["mcqs"]]
keys1 = dict(db.execute(text("SELECT id, correct_option FROM mcqs WHERE id = ANY(:i)"), {"i": ids1}).all())
st, _ = call(ADMIN, "POST", f"/api/quizzes/{q1['quiz_attempt_id']}/submit",
             {"answers": [{"mcq_id": i, "selected_option": keys1[i], "confidence": "sure"} for i in ids1]})
check("batch 1 submitted", st == 200, f"({st})")
st, q2 = call(ADMIN, "POST", "/api/quizzes/start", f)
ids2 = [m["id"] for m in q2["mcqs"]]
check("Continue gives the next unanswered batch", st == 200 and not (set(ids1) & set(ids2))
      and q2["unseen_in_scope"] == q1["unseen_in_scope"] - len([i for i in ids1]),
      f"(unseen {q1['unseen_in_scope']} -> {q2['unseen_in_scope']})")
print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
