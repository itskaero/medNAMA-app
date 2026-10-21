"""End-to-end TOS preparation through the API, as a throwaway student who is deleted afterwards.

Covers: blueprint, refresher + notes (real retrieval + LLM, cached after the first call), strict topic practice,
session submit recorded as answer events, progress, the coverage audit, a mock built and opened through the
shared /api/mocks/{id}/start flow, and (with --generate) on-demand questions through the generation pipeline.

    cd backend && ..\\.venv-gpu\\Scripts\\python.exe tests/e2e/e2e_tos.py [--generate]

Spends a few LLM calls (refresher/notes for one topic, if not cached; +1 generation with --generate).
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.auth import create_access_token  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402

EXAM, PAPER = "pget", "mbbs-p1"
ok = True


def check(cond: bool, what: str) -> None:
    global ok
    print(("PASS " if cond else "FAIL ") + what)
    ok = ok and cond


def main() -> int:
    gen = "--generate" in sys.argv
    db = SessionLocal()
    user = User(username=f"tos_e2e_{uuid.uuid4().hex[:8]}", password_hash="x", role="student")
    db.add(user)
    db.commit()
    db.refresh(user)
    c = TestClient(app)
    h = {"Authorization": f"Bearer {create_access_token({'sub': user.username})}"}
    try:
        tree = c.get("/api/tos/paper", params={"exam_id": EXAM, "paper_id": PAPER}, headers=h).json()
        check(tree.get("tagged") is True and tree["weighted"] is True, "paper is mapped and weighted")
        subj = next(s for s in tree["subjects"] if s["subject_name"] == "Physiology")
        topic = max(subj["topics"], key=lambda t: t["bank_mcq_count"] or 0)
        where = {"exam_id": EXAM, "paper_id": PAPER, "topic_id": topic["id"]}
        print(f"     topic: {subj['subject_name']} / {topic['topic_name']} ({topic['bank_mcq_count']} Qs)")

        r = c.get("/api/tos/topic/refresher", params=where, headers=h)
        body = r.json()
        check(r.status_code == 200 and body.get("available") and len(body["markdown"]) > 200 and body["citations"],
              f"refresher grounded in {len(body.get('citations', []))} textbook passages")
        r2 = c.get("/api/tos/topic/refresher", params=where, headers=h).json()
        check(r2.get("cached") is True, "refresher cached on revisit")

        n = c.get("/api/tos/topic/notes", params=where, headers=h).json()
        check(n.get("available") and (n.get("sheets") or n.get("markdown")), f"notes available (origin: {n.get('origin')})")

        m = c.get("/api/tos/topic/mcqs", params={**where, "limit": 10, "difficulty": "adaptive"}, headers=h).json()
        qs = m["questions"]
        check(len(qs) == min(10, m["available"]) and m["adaptive_note"], f"practice: {len(qs)} of {m['available']} · {m['adaptive_note']}")

        answers = [{"mcq_id": q["id"], "selected_option": (q["correct_option"] if i % 2 else "Z")} for i, q in enumerate(qs)]
        answers[-1]["selected_option"] = ""   # one left unanswered
        res = c.post("/api/tos/session/submit", json={**where, "mode": "exam", "answers": answers}, headers=h).json()
        check(res["total_attempted"] == len(qs) and res["answered"] == len(qs) - 1, "submit marks every question")
        events = db.execute(text("select count(*) from answer_events where user_id=:u and source='tos'"), {"u": user.id}).scalar()
        check(events == len(qs) - 1, f"{events} answers recorded as answer events (unanswered not recorded)")

        tree2 = c.get("/api/tos/paper", params={"exam_id": EXAM, "paper_id": PAPER}, headers=h).json()
        t2 = next(t for s in tree2["subjects"] for t in s["topics"] if t["id"] == topic["id"])
        check(t2["attempted_count"] == len(qs) - 1 and t2["accuracy"] is not None, f"topic shows {t2['attempted_count']} answers, {t2['accuracy']}%")

        p = c.get("/api/tos/progress", params={"exam_id": EXAM, "paper_id": PAPER}, headers=h).json()
        check(p["topics_practised"] == 1 and p["notes_opened"] >= 1 and p["suggestions"], "progress: 1 topic, notes opened, suggestions")

        a = c.get("/api/tos/mock/audit", params={"exam_id": EXAM, "paper_id": PAPER}, headers=h).json()
        check(a["planned_questions"] == 100 and all(q["quota_source"] == "official" for q in a["quotas"]),
              f"audit: official 100-question blueprint, compliant={a['is_compliant']}")

        b = c.post("/api/tos/mock/build", params={"exam_id": EXAM, "paper_id": PAPER, "mini_subject": subj["id"]}, headers=h).json()
        check(b.get("mock_id") and b["questions"] == min(subj["official_mcq_count"], subj["bank_mcq_count"]), f"mini-mock built: {b.get('title')}")
        s = c.post(f"/api/mocks/{b['mock_id']}/start", headers=h)
        check(s.status_code == 200 and len(s.json().get("questions", [])) == b["questions"], "mini-mock opens in the shared paper flow")

        if gen:
            g = c.post("/api/tos/topic/generate-mcqs", json={**where, "count": 2}, headers=h)
            gb = g.json()
            check(g.status_code == 200 and gb["generated_count"] >= 1, f"generated {gb.get('generated_count')} ({gb.get('grounding_counts')})")
            if g.status_code == 200:
                m2 = c.get("/api/tos/topic/mcqs", params={**where, "limit": 100}, headers=h).json()
                check(m2["available"] >= m["available"] + gb["generated_count"] - 0, "generated questions are tagged to the topic")
    finally:
        uid = user.id
        db.rollback()
        for sql in ("delete from answer_events where user_id=:u", "delete from concept_reviews where user_id=:u",
                    "delete from weekly_mock_entries where user_id=:u", "delete from weekly_mocks where track like :t",
                    "delete from study_sessions where user_id=:u", "delete from users where id=:u"):
            db.execute(text(sql), {"u": uid, "t": f"u{uid}:%"})
        db.commit()
        db.close()
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
