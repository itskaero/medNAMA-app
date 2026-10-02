"""HTTP e2e through :3000: revision sheets — the picker (books + chapter search) and a
book-scope sheet (cited bullets, figures, coverage, caching).

Pass --no-llm to skip writing a fresh sheet (the picker, validation and cache checks
still run against whatever sheets already exist).
"""
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
LLM = "--no-llm" not in sys.argv


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
INLINE = re.compile(r"\[([^\[\]\n]{2,80}?),\s*(?:Page|p\.)\s*(\d{1,4})\]", re.I)

print("Revision picker")
st, books = call(ADMIN, "GET", "/api/study/revision-books")
ready = [b for b in books if b["title"].startswith("Davidson")]
check("ready books listed, tiny payload", st == 200 and len(books) >= 5 and all("chapters" not in b for b in books))
check("books carry heading counts", st == 200 and all(b.get("chapter_count", 0) > 10 for b in books))
print("   " + ", ".join(f"{b['title']} ({b['chapter_count']})" for b in books[:6]))
st, keys = call(STUDENT, "GET", "/api/study/revision-books")
check("student can list books", st == 200 and len(keys) == len(books))

davidson = next((b for b in books if b["title"].startswith("Davidson")), None)
if davidson:
    st, found = call(ADMIN, "GET", f"/api/study/revision-chapters?book_ids={davidson['id']}&q=sepsis")
    check("chapter search finds headings", st == 200 and any("Sepsis" in h for h in found), f"({[h[:40] for h in found[:4]]})")
    st, allh = call(ADMIN, "GET", f"/api/study/revision-chapters?book_ids={davidson['id']}&q=")
    check("empty query is a ranked shortlist", st == 200 and 1 <= len(allh) <= 60)
    ids = ",".join(str(b["id"]) for b in books[:3])
    st, multi = call(ADMIN, "GET", f"/api/study/revision-chapters?book_ids={ids}&q=renal")
    check("multi-book search works", st == 200 and len(multi) > 0)
else:
    check("Davidson in the library", False, "(not found)")
    davidson = books[0]

print("Revision sheet validation")
st, _ = call(ADMIN, "POST", "/api/study/revision-sheet", {"book_ids": [], "topic": "x"})
check("empty book list rejected", st == 400, f"({st})")
st, _ = call(ADMIN, "POST", "/api/study/revision-sheet", {"book_ids": [davidson["id"]]})
check("book with no chapter and no topic rejected", st == 400, f"({st})")
st, _ = call(ADMIN, "POST", "/api/study/revision-sheet", {"book_ids": [davidson["id"]], "chapter": "not a real heading zzz"})
check("unknown chapter gives a clear error", st == 503, f"({st})")

scope = {"book_ids": [davidson["id"]], "topic": "oxygen delivery", "length": "quick"}
if LLM:
    db.execute(text("DELETE FROM topic_summaries WHERE scope_key LIKE 'books:%'"))
    db.commit()
    t0 = time.time()
    st, s1 = call(ADMIN, "POST", "/api/study/revision-sheet", scope)
    dt = time.time() - t0
    md = s1.get("markdown", "")
    refs = re.findall(INLINE, md)
    check("sheet written, cited and with figures", st == 200 and not s1.get("cached") and len(md) > 600
          and len(s1.get("citations", [])) > 0 and "## " in md,
          f"({dt:.0f}s, {len(md)} chars, {len(refs)} inline refs, {len(s1.get('figures', []))} figures)")
    check("coverage reported", s1.get("coverage", {}).get("kind") == "topic"
          and s1["coverage"]["read"] > 0, f"({s1.get('coverage')})")
    check("every inline reference is a checked citation",
          all((t.strip(), int(p)) in {(c["book_title"], c["page_number"]) for c in s1.get("citations", [])}
              for t, p in refs), f"({len(refs)} inline)")
    t0 = time.time()
    st, s2 = call(ADMIN, "POST", "/api/study/revision-sheet", scope)
    check("second call served from cache", st == 200 and s2.get("cached") and time.time() - t0 < 5,
          f"({time.time() - t0:.1f}s)")
    st, s3 = call(ADMIN, "POST", "/api/study/revision-sheet", {**scope, "length": "full"})
    check("a different length is a different sheet", st == 200 and not s3.get("cached")
          and s3.get("markdown", "") != md, f"({len(s3.get('markdown', ''))} chars)")
    if st == 200:
        print("   ---- preview ----")
        for line in s3["markdown"].splitlines()[:12]:
            print("   " + line[:140])
else:
    st, s1 = call(ADMIN, "POST", "/api/study/revision-sheet", scope)
    if st == 200 and s1.get("markdown"):
        check("cached sheet renders", True, f"(cached={s1.get('cached')}, {len(s1.get('markdown', ''))} chars)")
    else:
        check("--no-llm mode: nothing pre-cached, skipped", True, "")

print("\nSaved sheets (Study Corner)")
st, saved = call(STUDENT, "GET", "/api/study/revision-sheets/saved")
check("saved list is well-formed",
      st == 200 and isinstance(saved, list)
      and all({"id", "label", "book_ids", "chapter", "topic", "length", "created_at"} <= set(s) for s in saved))
if LLM:
    st_admin, saved_admin = call(ADMIN, "GET", "/api/study/revision-sheets/saved")
    mine = lambda rows: [s for s in rows if s.get("book_ids") == [davidson["id"]] and s.get("topic") == "oxygen delivery"]  # noqa: E731
    check("just-written sheet is saved for its author",
          st_admin == 200 and len(mine(saved_admin)) >= 1 and any(s.get("length") == "quick" for s in mine(saved_admin)))
    my = mine(saved_admin)[0]
    st, _ = call(ADMIN, "DELETE", f"/api/study/revision-sheets/saved/{my['id']}")
    check("saved sheet removed", st == 204, f"({st})")
    st, after = call(ADMIN, "GET", "/api/study/revision-sheets/saved")
    check("list drops the removed sheet", st == 200 and all(s["id"] != my["id"] for s in after))
    st, _ = call(ADMIN, "POST", "/api/study/revision-sheet", scope)
    st, again = call(ADMIN, "GET", "/api/study/revision-sheets/saved")
    check("reopening a sheet re-adds it to Study Corner", st == 200 and len(mine(again)) >= 1)
else:
    check("--no-llm mode: saved-sheet membership checks skipped", True, "")

print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)