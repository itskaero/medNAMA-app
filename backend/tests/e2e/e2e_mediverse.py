"""HTTP e2e through :3000 for the two-archive past papers (Radiant + MediVerse) and Past-paper Twists.

Needs: seed_mediverse.py run + embedded + classified, link_recalls.py and rank_past_papers.py run.
Pass --no-llm to skip the checks that call the AI (check-key, twists)."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402

BASE = os.environ.get("MEDNAMA_BASE_URL", "http://localhost:3000")
ADMIN = os.environ["MEDNAMA_ADMIN_TOKEN"]
STUDENT = os.environ["MEDNAMA_STUDENT_TOKEN"]
LLM = "--no-llm" not in sys.argv


def call(token, method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
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
q1 = lambda sql, **kw: db.execute(text(sql), kw).scalar()  # noqa: E731

print("Import")
n_mv = q1("SELECT count(*) FROM mcqs WHERE source = 'pastpaper:mediverse'")
check("MediVerse rows imported", n_mv > 21000, f"({n_mv})")
check("all embedded", q1("SELECT count(*) FROM mcqs WHERE source = 'pastpaper:mediverse' AND stem_embedding IS NULL") == 0)
mixed = q1("SELECT count(*) FROM mcqs WHERE source = 'pastpaper:mediverse' AND sub_category = 'Mixed'")
check("subjects classified (under 30% left Mixed)", mixed < n_mv * 0.30, f"({mixed} Mixed)")
check("every row restricted", q1("SELECT count(*) FROM mcqs WHERE source = 'pastpaper:mediverse' AND access <> 'restricted'") == 0)
groups = q1("SELECT count(DISTINCT recall_group) FROM mcqs WHERE recall_group IS NOT NULL")
cross = q1("SELECT count(*) FROM (SELECT recall_group FROM mcqs WHERE recall_group IS NOT NULL GROUP BY 1 "
           "HAVING count(DISTINCT source) > 1) g")
check("recall groups linked across archives", groups > 0 and cross > 0, f"({groups} groups, {cross} span both)")

print("Sittings, archive, Dentistry")
st, ov = call(ADMIN, "GET", "/api/past-papers")
fcps = next(e for e in ov["exams"] if e["exam"] == "FCPS Part 1")
sitting = next(p for y in fcps["years"] if y["year"] == 2019 for p in y["papers"] if p.get("source") == "MediVerse")
st, sc = call(ADMIN, "POST", "/api/past-papers/scope", {"exam": "FCPS Part 1", "tags": {"paper": [str(sitting["id"])]}})
check("one sitting's scope = its question count", st == 200 and sc["count"] == sitting["total"],
      f"({sitting['title']}: {sc.get('count')} vs {sitting['total']})")
st, quiz = call(ADMIN, "POST", "/api/quizzes/start", {"past_paper_exam": "FCPS Part 1", "tags": {"paper": [str(sitting["id"])]},
                                                      "num_questions": 20})
ids = [q["id"] for q in quiz.get("mcqs", [])]
inpaper = q1("SELECT count(*) FROM past_paper_questions WHERE paper_id = :p AND mcq_id = ANY(:i)", p=sitting["id"], i=ids)
check("practising a sitting serves only its questions", st == 200 and ids and inpaper == len(ids), f"({inpaper}/{len(ids)})")
st, quiz = call(ADMIN, "POST", "/api/quizzes/start", {"past_paper_exam": "FCPS Part 1", "tags": {"source": ["MediVerse"]},
                                                      "num_questions": 30})
check("archive filter", st == 200 and all(q.get("archive") == "MediVerse" for q in quiz["mcqs"]),
      f"({ {q.get('archive') for q in quiz.get('mcqs', [])} })")
st, quiz = call(ADMIN, "POST", "/api/quizzes/start", {"past_paper_exam": "FCPS Part 1", "num_questions": 200})
# A Dentistry question that is also in an FCPS Part 1 paper (e.g. the Old Pool) belongs to both exams.
served = [q["id"] for q in quiz.get("mcqs", [])]
dent_only = q1("SELECT count(*) FROM unnest(CAST(:i AS int[])) AS s(id) WHERE NOT EXISTS (SELECT 1 FROM past_paper_questions q "
               "JOIN past_papers p ON p.id = q.paper_id WHERE q.mcq_id = s.id AND p.exam = 'FCPS Part 1')", i=served)
check("FCPS Part 1 practice has no Dentistry-only questions", st == 200 and served and dent_only == 0, f"({dent_only} of {len(served)})")
qids = [q["id"] for q in quiz["mcqs"]]
dup = q1("SELECT count(*) - count(DISTINCT coalesce(recall_group, -id)) FROM mcqs WHERE id = ANY(:i)", i=qids)
check("a session never holds two versions of one question", dup == 0, f"({dup})")
st, dd = call(ADMIN, "POST", "/api/quizzes/start", {"past_paper_exam": "FCPS Part 1 (Dentistry)", "num_questions": 10})
check("Dentistry exam practisable on its own", st == 200 and len(dd["mcqs"]) == 10)
dental_ids = set(db.execute(text("SELECT id FROM mcqs WHERE main_category = 'Past papers · FCPS Part 1 (Dentistry)'")).scalars())
st, dose = call(ADMIN, "GET", "/api/study/daily")
dose_ids = {(i.get("mcq") or {}).get("id") for i in dose.get("items", []) if isinstance(i, dict)}
check("Daily Dose leaves Dentistry-only questions out", st == 200 and not (dose_ids & dental_ids), f"({st}, {len(dose_ids)} items)")

print("Recall groups")
gid = q1("SELECT m.recall_group FROM mcqs m WHERE m.recall_group IS NOT NULL GROUP BY 1 HAVING count(DISTINCT m.source) > 1 "
         "AND NOT EXISTS (SELECT 1 FROM answer_events e JOIN mcqs x ON x.id = e.mcq_id WHERE x.recall_group = m.recall_group) "
         "ORDER BY 1 LIMIT 1")   # a group nobody has answered yet, so the check works on re-runs
a, b = db.execute(text("SELECT id FROM mcqs WHERE recall_group = :g ORDER BY id LIMIT 2"), {"g": gid}).scalars().all()
st, rv = call(ADMIN, "GET", f"/api/mcqs/{a}/recalls")
check("recalls endpoint lists the other version", st == 200 and b in [v["id"] for v in rv["versions"]]
      and all("same_key" in v for v in rv["versions"]), f"({st})")
paper_of_b, exam_of_b = db.execute(text("SELECT p.id, p.exam FROM past_paper_questions q JOIN past_papers p ON p.id = q.paper_id "
                                         "WHERE q.mcq_id = :m LIMIT 1"), {"m": b}).one()
scope_tags = {"paper": [str(paper_of_b)]}
st, before = call(ADMIN, "POST", "/api/past-papers/scope", {"exam": exam_of_b, "tags": scope_tags})
key_a = q1("SELECT correct_option FROM mcqs WHERE id = :i", i=a)
st, _ = call(ADMIN, "POST", "/api/study/answer", {"mcq_id": a, "selected_option": key_a, "confidence": "sure"})
st2, after = call(ADMIN, "POST", "/api/past-papers/scope", {"exam": exam_of_b, "tags": scope_tags})
check("answering one version counts for its group in another archive's paper", st == 200 and st2 == 200
      and after["answered"] == before["answered"] + 1, f"({before.get('answered')} -> {after.get('answered')})")
st, s1 = call(ADMIN, "POST", "/api/quizzes/start", {"past_paper_exam": exam_of_b, "tags": scope_tags, "num_questions": 500})
order = [q["id"] for q in s1["mcqs"]]
check("the answered question's other version is not served as unseen", b not in order[:max(1, s1["unseen_in_scope"])],
      f"(unseen {s1.get('unseen_in_scope')})")

if (settings.past_papers_access or "all").lower() == "all":
    print("Student (PAST_PAPERS_ACCESS=all)")
    st, _ = call(STUDENT, "GET", f"/api/mcqs/{a}/recalls")
    check("recalls open to students", st == 200, f"({st})")
else:
    print("Student (PAST_PAPERS_ACCESS=admin)")
    st, _ = call(STUDENT, "GET", f"/api/mcqs/{a}/recalls")
    check("recalls locked", st == 403, f"({st})")
    st, _ = call(STUDENT, "POST", f"/api/mcqs/{a}/twists")
    check("twists locked", st == 403, f"({st})")
    st, _ = call(STUDENT, "POST", f"/api/mcqs/{a}/check-key")
    check("check-key locked", st == 403, f"({st})")

if LLM:
    print("Key check (Answer-Key Referee)")
    conflict = q1("SELECT mcq_id FROM mcq_tags WHERE axis = 'flag' AND label = 'key-conflict' LIMIT 1")
    if conflict:
        st, ck = call(ADMIN, "POST", f"/api/mcqs/{conflict}/check-key")
        check("check-key returns a verdict", st == 200 and "verdict" in ck, f"({st} {ck.get('verdict')})")

    print("Twists")
    seed = q1("SELECT m.id FROM mcqs m WHERE m.main_category = 'Past papers · FCPS Part 1' AND m.asked_years IS NOT NULL "
              "AND jsonb_array_length(m.asked_years) >= 2 AND NOT EXISTS (SELECT 1 FROM mcqs t WHERE t.twist_of = m.id) "
              "ORDER BY jsonb_array_length(m.asked_years) DESC, m.id LIMIT 1")
    t0 = time.time()
    st, tw = call(ADMIN, "POST", f"/api/mcqs/{seed}/twists")
    while tw.get("status") == "running" and time.time() - t0 < 600:
        time.sleep(5)
        st, tw = call(ADMIN, "GET", f"/api/mcqs/{seed}/twists")
    twists = tw.get("twists") or []
    print(f"     seed {seed}: {tw.get('status')} in {time.time() - t0:.0f}s {tw.get('detail', '')}")
    check("twists written", tw.get("status") == "done" and 1 <= len(twists) <= 3, f"({len(twists)})")
    seed_answer = q1("SELECT options->>correct_option FROM mcqs WHERE id = :i", i=seed)
    from app.past_papers import answer_norm, answers_agree  # noqa: E402

    check("every twist asks something with a different answer",
          all(not answers_agree(answer_norm(t["options"][t["correct_option"]]), answer_norm(seed_answer)) for t in twists))
    check("twists typed, refereed, never contradicted", all(t["twist_type"] and t["referee"] not in ("contradicted", "books_conflict")
                                                           for t in twists), f"({[(t['twist_type'], t['referee'], t['grounding']) for t in twists]})")
    check("twists stored against the seed, restricted like it",
          q1("SELECT count(*) FROM mcqs WHERE twist_of = :s AND access = 'restricted' AND main_category = 'Past-paper twists'", s=seed) == len(twists))
    for t in twists:
        print(f"     [{t['twist_label']} · {t['grounding']} · {t['referee']}] {' '.join(t['question_text'].split())[:130]} => {t['options'][t['correct_option']]}")
    t1 = time.time()
    st, again = call(ADMIN, "POST", f"/api/mcqs/{seed}/twists")
    check("asking again returns the stored twists at once", again.get("status") == "done"
          and [t["id"] for t in again["twists"]] == [t["id"] for t in twists] and time.time() - t1 < 5)
    pp = q1("SELECT paper_id FROM past_paper_questions WHERE mcq_id = :m LIMIT 1", m=seed)
    # Enough room for every twist of the sitting (a batch run may already have written many).
    st, tq = call(ADMIN, "POST", "/api/quizzes/start", {"past_paper_exam": None, "tags": {"paper": [str(pp)]}, "twists": True,
                                                        "num_questions": 500})
    check("Twists practice serves the scope's twists", st == 200 and {t["id"] for t in twists} <= {q["id"] for q in tq["mcqs"]}
          and all(q["main_category"] == "Past-paper twists" for q in tq["mcqs"]), f"({st})")
    bare = db.execute(text("SELECT p.id, p.exam FROM past_papers p WHERE NOT EXISTS (SELECT 1 FROM past_paper_questions q "
                           "JOIN mcqs t ON t.twist_of = q.mcq_id WHERE q.paper_id = p.id) ORDER BY p.id LIMIT 1")).first()
    st, none = call(ADMIN, "POST", "/api/quizzes/start", {"past_paper_exam": bare[1], "tags": {"paper": [str(bare[0])]},
                                                          "twists": True, "num_questions": 5})
    check("no twists yet -> a clear message", st == 400 and "Twist it" in none.get("detail", ""), f"({st})")

print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
