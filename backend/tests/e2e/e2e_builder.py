"""HTTP e2e through :3000: the Custom exam builder (Paper 1 / Paper 2), Programme exams (FCPS-I as
two papers of 100), Utilities (Check an answer, High-yield map open to students) and reopening a
saved revision sheet under the programme it was written for.

Pass --no-llm to skip the Check-an-answer call (it runs the referee).
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

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
            return r.status, json.loads(raw) if raw else None
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


print("Builder tree")
st, tree = call(STUDENT, "GET", "/api/practice/tree")
names = [s["specialty"] for s in tree.get("specialties", [])] if st == 200 else []
check("tree lists Paper 1 subjects and Paper 2 specialties",
      st == 200 and len(tree["subjects"]) >= 7 and {"Medicine", "Surgery", "Gynaecology & Obstetrics"} <= set(names),
      f"({len(names)} specialties)")
check("every specialty draws on the bank or the past papers",
      st == 200 and all(s["bank"] + s["past"] > 0 for s in tree["specialties"]))

print("Paper 1 / Paper 2 previews")
st, p1 = call(STUDENT, "POST", "/api/mocks/custom/preview", {"scope": {"paper": "1"}, "questions": 100})
check("all of Paper 1 is startable, mixed by subject",
      st == 200 and p1["can_start"] and len(p1["by_subject"]) >= 7, f"(total={p1.get('total')})")
st, p1a = call(STUDENT, "POST", "/api/mocks/custom/preview", {"scope": {"paper": "1", "subjects": ["Anatomy"]}})
check("Paper 1 narrows to one subject", st == 200 and [s["subject"] for s in p1a["by_subject"]] == ["Anatomy"])
st, p2 = call(STUDENT, "POST", "/api/mocks/custom/preview",
              {"scope": {"paper": "2", "specialties": ["ENT", "Psychiatry"]}, "questions": 100})
check("Paper 2 is mixed by specialty", st == 200 and {s["subject"] for s in p2["by_subject"]} == {"ENT", "Psychiatry"},
      f"({p2.get('by_subject')})")
st, legacy = call(STUDENT, "POST", "/api/mocks/custom/preview", {"scope": {"sources": ["past"], "subjects": ["Physiology"]}})
check("a scope with no paper (older clients) still means Paper 1", st == 200 and legacy["total"] > 0)

print("Programme exams")
st, prog = call(STUDENT, "GET", "/api/mocks/programmes")
by = {p["programme"]: p for p in prog.get("programmes", [])} if st == 200 else {}
f1 = by.get("FCPS-I", {})
check("FCPS-I is two papers of 100", [(x["key"], x["questions"]) for x in f1.get("papers", [])] == [("paper1", 100), ("paper2", 100)])
check("FCPS-I Paper 2 asks for a specialty and lists them",
      len(f1.get("papers", [])) == 2 and f1["papers"][1]["needs_specialty"] and len(f1["papers"][1]["specialties"]) >= 8)
check("programmes with no content are not startable",
      all(not by[c]["can_start"] for c in ("MD", "MS", "USMLE") if c in by))
st, paper = call(STUDENT, "POST", "/api/mocks/custom",
                 {"programme": "FCPS-I", "part": "paper2", "specialty": "Medicine", "questions": 100, "minutes": 120})
check("FCPS-I Paper 2 (Medicine) builds 100 questions", st == 200 and paper.get("questions") == 100
      and paper.get("subjects") == ["Medicine"], f"({paper.get('title')})")
if st == 200:
    st, started = call(STUDENT, "POST", f"/api/mocks/{paper['mock_id']}/start")
    check("the built paper starts through the shared mock flow", st == 200, f"({st})")

print("Utilities")
st, freq = call(STUDENT, "GET", "/api/recalls/frequency")
check("High-yield map open to students", st == 200, f"({st})")
st, _ = call(STUDENT, "GET", "/api/recalls?limit=1")
check("recall bank stays admin-only", st == 403, f"({st})")
if LLM:
    st, rf = call(STUDENT, "POST", "/api/referee",
                  {"question": "Which cranial nerve supplies the lateral rectus muscle?",
                   "options": {"A": "Oculomotor", "B": "Trochlear", "C": "Abducent", "D": "Facial", "E": "Trigeminal"},
                   "key": "C"})
    check("students can check an answer", st == 200 and rf.get("verdict"), f"({st} {rf.get('verdict')})")
    check("a full MCQ is matched against or kept in the bank",
          st == 200 and (rf.get("bank") or {}).get("status") in ("exists", "added", "pending", "skipped"),
          f"({(rf or {}).get('bank')})")
else:
    check("--no-llm mode: Check an answer skipped", True)

print("Revision sheets keep their programme")
st, saved = call(STUDENT, "GET", "/api/study/revision-sheets/saved")
check("saved sheets report their level", st == 200 and all("level" in s for s in saved), f"({len(saved or [])} saved)")

print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
