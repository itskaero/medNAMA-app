"""HTTP e2e for account data export and deletion (revision.md §10: "data export and deletion").

Registers a throwaway student, gives it real rows across several tables (a note, a flashcard,
a concept bookmark, an MCQ bookmark), then verifies that:
  * the self-service export returns that account's own data;
  * self-deletion removes the account and every one of its rows, and invalidates the token;
  * admin deletion removes another throwaway account, and admins cannot delete themselves.

The shared admin/student accounts are never modified.
"""
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
PASSWORD = "testpass123"


def call(token, method, path, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method)
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


def mint(username):
    from datetime import timedelta
    from app.auth import create_access_token
    return create_access_token({"sub": username}, timedelta(hours=1))


def register_user(username, password, invite):
    """Register a throwaway account, waiting out the register rate limit (5/60s per IP).

    The limiter is keyed by client IP and in-memory, so running this suite repeatedly in a
    short span trips it; backing off and retrying keeps the test reliable without weakening
    the production limit.
    """
    for _ in range(12):  # up to ~120s
        st, body = call(None, "POST", "/api/auth/register",
                        {"username": username, "password": password, "invite_code": invite})
        if st == 429:
            time.sleep(10)
            continue
        return st, body
    return st, body


def scalar(sql, params=None):
    db = SessionLocal()
    try:
        return db.execute(text(sql), params or {}).scalar()
    finally:
        db.close()


print("Register a throwaway student")
ts = int(time.time())
username = f"e2e_acct_{ts}"
st, inv = call(ADMIN, "POST", "/api/admin/invites", {"label": "e2e account", "max_uses": 10, "days": 1})
invite = inv.get("code") if st == 200 else None
check("admin created an invite", st == 200 and bool(invite), f"({st})")

st, reg = register_user(username, PASSWORD, invite)
check("registered throwaway student", st == 201 and reg.get("username") == username, f"({st})")
token = mint(username)

print("Create data across several tables")
st, note = call(token, "POST", "/api/notes", {"title": "e2e note", "content": "e2e note content",
                                              "book_title": "Bailey", "page_number": 1})
check("created a note", st == 201 and note.get("id"), f"({st})")
st, fc = call(token, "POST", "/api/flashcards", {"front": "e2e front", "back": "e2e back", "topic": "e2e"})
check("created a flashcard", st == 201 and fc.get("id"), f"({st})")
st, bm = call(token, "POST", "/api/bookmarks/concept", {"content": "e2e concept", "book_title": "Bailey", "page_number": 2})
check("created a concept bookmark", st == 200 and bm.get("id"), f"({st})")
mcq_id = scalar("SELECT id FROM mcqs WHERE access = 'open' ORDER BY id LIMIT 1")
if mcq_id:
    st, mb = call(token, "POST", f"/api/bookmarks/mcq/{mcq_id}")
    check("created an mcq bookmark", st == 200 and mb.get("bookmarked") is True, f"({st})")
else:
    print("  (no open mcq; skipped mcq bookmark)")

user_id = scalar("SELECT id FROM users WHERE username = :u", {"u": username})
check("throwaway user exists in db", user_id is not None)

print("Export returns the account's own data")
st, exp = call(token, "GET", "/api/auth/export")
check("export is 200 json", st == 200 and isinstance(exp, dict), f"({st})")
check("export profile is the throwaway user", exp.get("profile", {}).get("username") == username,
      f"({exp.get('profile', {}).get('username')})")
check("export contains the note", any(n.get("title") == "e2e note" for n in exp.get("notes", [])))
check("export contains the flashcard", any(f.get("front") == "e2e front" for f in exp.get("flashcards", [])))
check("export contains the concept bookmark",
      any(b.get("content") == "e2e concept" for b in exp.get("concept_bookmarks", [])))

print("Wrong password is refused")
st, _ = call(token, "DELETE", "/api/auth/account", {"password": "wrong"})
check("delete with wrong password is 400", st == 400, f"({st})")

print("Self-deletion removes the account and its data")
st, _ = call(token, "DELETE", "/api/auth/account", {"password": PASSWORD})
check("delete with correct password is 200", st == 200, f"({st})")
st, _ = call(token, "GET", "/api/auth/me")
check("token no longer works (401)", st == 401, f"({st})")
check("user row deleted", scalar("SELECT count(*) FROM users WHERE id = :u", {"u": user_id}) == 0)
check("notes deleted", scalar("SELECT count(*) FROM notes WHERE user_id = :u", {"u": user_id}) == 0)
check("flashcards deleted", scalar("SELECT count(*) FROM flashcards WHERE user_id = :u", {"u": user_id}) == 0)
check("concept bookmarks deleted",
      scalar("SELECT count(*) FROM concept_bookmarks WHERE user_id = :u", {"u": user_id}) == 0)
check("mcq bookmarks deleted",
      scalar("SELECT count(*) FROM mcq_bookmarks WHERE user_id = :u", {"u": user_id}) == 0)

print("Admin deletion removes another throwaway account")
username2 = f"e2e_acct_{ts}_b"
st, reg2 = register_user(username2, PASSWORD, invite)
check("registered second throwaway", st == 201, f"({st})")
token2 = mint(username2)
st, _ = call(token2, "POST", "/api/notes", {"title": "note2", "content": "content2"})
check("second throwaway created a note", st == 201, f"({st})")
uid2 = scalar("SELECT id FROM users WHERE username = :u", {"u": username2})
st, dele2 = call(ADMIN, "DELETE", f"/api/admin/users/{uid2}")
check("admin deleted the second throwaway", st == 200, f"({st})")
check("second user row deleted", scalar("SELECT count(*) FROM users WHERE id = :u", {"u": uid2}) == 0)
check("second user's notes deleted",
      scalar("SELECT count(*) FROM notes WHERE user_id = :u", {"u": uid2}) == 0)

print("Admin cannot delete itself")
admin_id = scalar("SELECT id FROM users WHERE username = 'admin'")
st, _ = call(ADMIN, "DELETE", f"/api/admin/users/{admin_id}")
check("admin self-delete is refused", st == 400, f"({st})")

print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
