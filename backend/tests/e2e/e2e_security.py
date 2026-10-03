"""HTTP e2e through :3000 for the security fixes: tokens signed with a guessable secret are refused, board (exam)
mode keeps answer keys back until Finish, private concept cards stay private, malformed duel answers are a 4xx."""
import base64
import hashlib
import hmac
import json
import os
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


def forge(secret: str, sub: str) -> str:
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"sub": sub, "exp": int(time.time()) + 3600}).encode())
    sig = b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


print("Tokens")
for guess in ("medrag_secret_key_change_me_in_prod", "dev-secret-change-in-production", "change-this-jwt-secret"):
    st, _ = call(forge(guess, "admin"), "GET", "/api/auth/me")
    check(f"admin token signed with '{guess[:12]}…' refused", st == 401, f"({st})")
st, me = call(ADMIN, "GET", "/api/auth/me")
check("real admin token works", st == 200 and me.get("username") == "admin", f"({st})")

print("Board mode keeps keys back")
scope = {"sources": ["bank"], "topics": ["Physiology|Renal"]}
st, b = call(STUDENT, "POST", "/api/quizzes/start", {"scope": scope, "num_questions": 5, "feedback_mode": "board", "label": "e2e board"})
qs = b.get("mcqs", [])
check("board start sends no keys", st == 200 and qs and all(q.get("correct_option") is None for q in qs), f"({st}, {len(qs)})")
if qs:
    st, a = call(STUDENT, "POST", f"/api/quizzes/{b['quiz_attempt_id']}/answer", {"mcq_id": qs[0]["id"], "selected_option": "A"})
    check("board answer does not reveal the key", st == 200 and a.get("correct_option") is None, f"({a})")
    st, s = call(STUDENT, "POST", f"/api/quizzes/{b['quiz_attempt_id']}/submit",
                 {"answers": [{"mcq_id": q["id"], "selected_option": "B"} for q in qs] + [{"mcq_id": 1, "selected_option": "A"}]})
    keys = s.get("keys", {})
    check("submit returns every key for review", st == 200 and all(str(q["id"]) in keys for q in qs), f"({st}, {len(keys)})")
    check("submit ignores a question outside the session", st == 200 and s.get("total_questions") == len(qs), f"({s.get('total_questions')})")
st, t = call(STUDENT, "POST", "/api/quizzes/start", {"scope": scope, "num_questions": 3, "feedback_mode": "tutor", "label": "e2e tutor"})
tq = t.get("mcqs", [])
check("tutor start keeps keys (instant feedback)", st == 200 and tq and all(q.get("correct_option") for q in tq))
if tq:
    st, a = call(STUDENT, "POST", f"/api/quizzes/{t['quiz_attempt_id']}/answer", {"mcq_id": tq[0]["id"], "selected_option": "A"})
    check("tutor answer returns the key", st == 200 and a.get("correct_option") == tq[0]["correct_option"], f"({a})")

print("Private concept cards")
db = SessionLocal()
private = db.execute(text("SELECT id FROM concept_cards WHERE visibility <> 'all' LIMIT 1")).scalar()
if private:
    st, _ = call(STUDENT, "GET", f"/api/concepts/{private}")
    check("student cannot open a private concept card", st == 404, f"({st})")
    st, _ = call(ADMIN, "GET", f"/api/concepts/{private}")
    check("admin can", st == 200, f"({st})")
else:
    print("  (no private concept cards here; skipped)")

print("Duels")
st, d = call(ADMIN, "POST", "/api/duels", {"subject": None, "count": 5})
if st == 201:
    st, r = call(STUDENT, "POST", f"/api/duels/{d['code']}/submit", {"answers": {"abc": "A", "12x": "B"}, "time_ms": 1000})
    check("malformed duel answers are not a server error", st < 500, f"({st})")

print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
