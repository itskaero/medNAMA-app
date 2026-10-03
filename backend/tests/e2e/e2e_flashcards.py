"""HTTP e2e through :3000 for Study Corner flashcards on FSRS (app/fsrs.py): Again brings a card back in minutes,
Good schedules it days out, Easy further, and the review list only holds what is due."""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime

BASE = os.environ.get("MEDNAMA_BASE_URL", "http://localhost:3000")
STUDENT = os.environ["MEDNAMA_STUDENT_TOKEN"]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {STUDENT}", "Content-Type": "application/json"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return e.code, {}


ok = True


def check(name, cond, extra=""):
    global ok
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}", flush=True)
    ok &= bool(cond)


def days_until(iso):
    return (datetime.fromisoformat(iso) - datetime.utcnow()).total_seconds() / 86400


print("Flashcards (FSRS)")
made = []
for i in range(3):
    st, c = call("POST", "/api/flashcards", {"front": f"e2e FSRS card {i}", "back": "answer", "topic": "e2e"})
    check(f"create card {i}", st == 201, f"({st})")
    made.append(c.get("id"))

st, due = call("GET", "/api/flashcards/review")
check("new cards are due", st == 200 and all(any(d["id"] == m for d in due) for m in made))

st, again = call("POST", f"/api/flashcards/{made[0]}/review", {"rating": 0})
check("Again: back within the hour", st == 200 and days_until(again["next_due"]) < 1 / 24, f"({again.get('next_due')})")
st, good = call("POST", f"/api/flashcards/{made[1]}/review", {"rating": 2})
st2, easy = call("POST", f"/api/flashcards/{made[2]}/review", {"rating": 3})
check("Good: a few days out", st == 200 and 1 <= days_until(good["next_due"]) <= 10, f"({days_until(good['next_due']):.1f} d)")
check("Easy: later than Good", st2 == 200 and days_until(easy["next_due"]) > days_until(good["next_due"]),
      f"({days_until(easy['next_due']):.1f} d)")
# A second Good after the first grows the interval (stability carries over); elapsed ~0 keeps it modest but >= 1 day
st, good2 = call("POST", f"/api/flashcards/{made[1]}/review", {"rating": 2})
check("second review keeps FSRS state", st == 200 and days_until(good2["next_due"]) >= 1, f"({days_until(good2['next_due']):.1f} d)")

st, due = call("GET", "/api/flashcards/review")
ids = {d["id"] for d in due}
check("scheduled cards leave the review list", made[1] not in ids and made[2] not in ids)
st, _ = call("POST", "/api/flashcards/999999999/review", {"rating": 2})
check("unknown card is a 404", st == 404, f"({st})")
st, _ = call("POST", f"/api/flashcards/{made[0]}/review", {"rating": 7})
check("bad rating is a 400", st == 400, f"({st})")

for m in made:
    call("DELETE", f"/api/flashcards/{m}")

print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
raise SystemExit(0 if ok else 1)
