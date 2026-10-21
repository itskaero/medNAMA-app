"""TOS preparation: blueprint parsing, topic mapping, strict topic practice and blueprint mocks.

Run from the repo root:  .venv-gpu\\Scripts\\python.exe -m pytest backend/tests/test_tos.py -q

The first group needs only the TOS files. The second reads the database named by DATABASE_URL (the dev DB)
and is skipped when it cannot be reached or the tagger has not been run; it writes nothing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tos import MINI_QUOTA, _allocate, _split, find_topic, tos_registry  # noqa: E402
from app.tos_tags import (paper_prefix, slug, specialty_key, subtopic_label,  # noqa: E402
                          topic_label)


# ─── Blueprint (files only) ───────────────────────────────────────────────────

def test_examinations_loaded_with_source_and_version():
    exams = {e["id"]: e for e in tos_registry.get_examinations_summary()}
    assert {"pget", "fcps1"} <= set(exams)
    for e in exams.values():
        assert e["source"] and e["version"], f"{e['id']} has no TOS source/version"


def test_pget_paper1_official_distribution_is_100():
    p = tos_registry.get_paper("pget", "mbbs-p1")
    assert p["weighted"] is True
    assert sum(s["official_mcq_count"] for s in p["subjects"]) == p["total_mcqs"] == 100


def test_pget_paper1_has_the_eight_basic_science_subjects():
    p = tos_registry.get_paper("pget", "mbbs-p1")
    names = {s["subject_name"] for s in p["subjects"]}
    assert len(names) == 8 and {"Anatomy", "Physiology", "Pathology", "Pharmacology"} <= names
    assert all(s["topics"] for s in p["subjects"])


def test_fcps_has_eleven_specialties_each_with_two_papers():
    papers = tos_registry.examinations["fcps1"]["papers"]
    specialties = {p["specialty"] for p in papers}
    assert len(specialties) == 11
    for sp in specialties:
        assert {p["paper"] for p in papers if p["specialty"] == sp} == {"Paper 1", "Paper 2"}
    p = tos_registry.get_paper("fcps1", "anaesthesiology-p1")
    assert p["specialty"] == "Anaesthesiology" and p["paper"] == "Paper 1"


def test_mbbs_modular_curriculum_loaded():
    papers = tos_registry.examinations["mbbs"]["papers"]
    assert papers and all(p["subjects"] for p in papers)
    assert any(p["id"].startswith("y5-") for p in papers), "Year 5 clerkships"


def test_examinations_endpoint_metadata():
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).get("/api/tos/examinations")
    assert r.status_code == 200
    pget = next(e for e in r.json() if e["id"] == "pget")
    assert "University of Health Sciences" in pget["authority"]
    assert all({"weighted", "available"} <= set(p) for p in pget["papers"])


def test_fcps_prints_no_subject_counts_and_is_not_weighted():
    for p in tos_registry.examinations["fcps1"]["papers"]:
        assert p["weighted"] is False
        assert all(s["official_mcq_count"] is None for s in p["subjects"]), "never invent an FCPS count"


def test_topic_ids_unique_and_inside_their_paper_prefix():
    """Tag labels are '<exam>:<topic id>'; a paper's re-tag deletes by prefix, so every topic must sit under it."""
    for exam_id, exam in tos_registry.examinations.items():
        for p in exam["papers"]:
            ids = [t["id"] for s in p["subjects"] for t in s["topics"]]
            assert len(ids) == len(set(ids)), f"duplicate topic id in {exam_id}/{p['id']}"
            prefix = paper_prefix(exam_id, p["id"])
            for tid in ids:
                assert topic_label(exam_id, tid).startswith(prefix), (exam_id, p["id"], tid)


def test_paper_prefixes_do_not_overlap():
    prefixes = [paper_prefix(e, p["id"]) for e, x in tos_registry.examinations.items() for p in x["papers"]]
    for a in prefixes:
        assert sum(1 for b in prefixes if b.startswith(a)) == 1, f"{a} is a prefix of another paper"


def test_find_topic_and_labels():
    p = tos_registry.get_paper("pget", "mbbs-p1")
    t = p["subjects"][0]["topics"][0]
    subj, topic = find_topic(p, t["id"])
    assert topic is t and subj is p["subjects"][0]
    assert subtopic_label("pget", t["id"], "Brachial Plexus!") == f"pget:{t['id']}#brachial-plexus"
    assert slug("  A & B ") == "a-b"


@pytest.mark.parametrize("n,weights", [(100, [3, 1, 1]), (7, [1, 1, 1]), (0, [2, 5]), (13, [10, 1, 1, 1])])
def test_split_sums_exactly(n, weights):
    out = _split(n, weights)
    assert sum(out) == n and len(out) == len(weights)
    assert out == sorted(out, reverse=True) or len(set(weights)) > 1


def test_allocation_official_internal_and_mini():
    weighted = tos_registry.get_paper("pget", "mbbs-p1")
    rows = _allocate(weighted, None)
    assert all(r["source"] == "official" for r in rows)
    assert sum(r["quota"] for r in rows) == 100

    unweighted = tos_registry.get_paper("fcps1", "medicine-allied-p1")
    rows = _allocate(unweighted, None)
    assert all(r["source"] == "internal" for r in rows), "an unweighted TOS must not be presented as official"
    assert sum(r["quota"] for r in rows) == unweighted["total_mcqs"]

    s = unweighted["subjects"][0]
    assert _allocate(unweighted, s) == [{"subject": s, "quota": MINI_QUOTA, "source": "internal"}]
    s = weighted["subjects"][0]
    assert _allocate(weighted, s)[0] == {"subject": s, "quota": s["official_mcq_count"], "source": "official"}


@pytest.mark.parametrize("specialty,key", [
    ("Medicine & Allied", "Medicine"), ("Otorhinolaryngology (ENT)", "ENT"), ("Obstetrics and Gynaecology",
     "Gynaecology & Obstetrics"), ("Oral & Maxillofacial Surgery", "Dentistry"), ("Anesthesia", "Anaesthesia"),
    ("Paediatric Medicine", None), ("Community Medicine", None), ("Diagnostic Radiology", "Radiology")])
def test_specialty_mapping(specialty, key):
    assert specialty_key({"specialty": specialty}) == key


# ─── Database (dev DB, read-only) ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def db():
    from app.database import SessionLocal
    from app.tos_tags import paper_tagged

    try:
        s = SessionLocal()
        s.execute(__import__("sqlalchemy").text("select 1"))
    except Exception as e:
        pytest.skip(f"database unreachable: {e}")
    if not paper_tagged(s, "pget", "mbbs-p1"):
        s.close()
        pytest.skip("run scripts/tag_tos.py first")
    yield s
    s.rollback()
    s.close()


def _topic_with_questions(db, exam_id="pget", paper_id="mbbs-p1"):
    from app.tos import get_paper_tree_with_metrics

    tree = get_paper_tree_with_metrics(db, exam_id, paper_id)
    for s in tree["subjects"]:
        for t in s["topics"]:
            if (t["bank_mcq_count"] or 0) >= 5:
                return tree, s, t
    pytest.skip("no topic with questions")


def test_tree_counts_are_consistent(db):
    tree, _, _ = _topic_with_questions(db)
    assert tree["tagged"] is True
    for s in tree["subjects"]:
        assert s["bank_mcq_count"] == sum(t["bank_mcq_count"] for t in s["topics"])
        assert s["official_mcq_count"] is not None
    assert tree["bank_mcq_count"] == sum(s["bank_mcq_count"] for s in tree["subjects"])


def test_one_topic_per_question_per_paper(db):
    from sqlalchemy import text

    for exam_id, paper_id in (("pget", "mbbs-p1"), ("pget", "p2-medicine-allied"), ("fcps1", "medicine-allied-p1")):
        n = db.execute(text(
            "SELECT count(*) FROM (SELECT mcq_id FROM mcq_tags WHERE axis='tos_topic' AND label LIKE :p "
            "GROUP BY mcq_id HAVING count(*) > 1) x"), {"p": paper_prefix(exam_id, paper_id) + "%"}).scalar()
        assert n == 0, f"{n} questions carry two topics in {exam_id}/{paper_id}"


def test_topic_practice_never_borrows_from_other_topics(db):
    from app.models import MCQTag
    from app.tos import fetch_topic_mcqs

    _, _, t = _topic_with_questions(db)
    res = fetch_topic_mcqs(db, None, "pget", "mbbs-p1", t["id"], limit=100)
    ids = [q["id"] for q in res["questions"]]
    assert ids and res["available"] >= len(ids)
    tagged = {r[0] for r in db.query(MCQTag.mcq_id).filter(MCQTag.axis == "tos_topic",
                                                           MCQTag.label == topic_label("pget", t["id"]))}
    assert set(ids) <= tagged
    assert len(ids) == len(set(ids))


def test_difficulty_filter_and_shortfall_is_reported(db):
    from app.tos import fetch_topic_mcqs

    _, _, t = _topic_with_questions(db)
    res = fetch_topic_mcqs(db, None, "pget", "mbbs-p1", t["id"], difficulty=5, limit=100)
    assert all(q["difficulty"] == 5 for q in res["questions"])
    assert len(res["questions"]) == min(res["available"], 100)   # all of them when short, never padded


def test_full_mock_follows_blueprint_without_duplicates(db):
    from app.models import MCQ
    from app.tos import plan_mock

    plan = plan_mock(db, None, "pget", "mbbs-p1", pick=True)
    ids = plan["mcq_ids"]
    assert len(ids) == len(set(ids)) == plan["supplied_questions"]
    assert plan["planned_questions"] == 100
    groups = [g for (g,) in db.query(MCQ.recall_group).filter(MCQ.id.in_(ids), MCQ.recall_group.isnot(None))]
    assert len(groups) == len(set(groups)), "two versions of one recalled question in a mock"
    for q in plan["quotas"]:
        assert q["supplied"] <= q["quota"]
        assert q["sufficient"] == (q["deviation"] == 0)
    assert plan["is_compliant"] == (not plan["deviations"])


def test_unweighted_mock_is_never_called_compliant(db):
    from app.tos import plan_mock

    plan = plan_mock(db, None, "fcps1", "medicine-allied-p1")
    assert plan["weighted"] is False and plan["is_compliant"] is False
    assert "no question counts" in plan["compliance_note"]


def test_refresher_and_notes_need_sign_in():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    assert c.get("/api/tos/examinations").status_code == 200
    for path in ("/api/tos/topic/refresher", "/api/tos/topic/notes"):
        r = c.get(path, params={"exam_id": "pget", "paper_id": "mbbs-p1", "topic_id": "x"})
        assert r.status_code == 401, f"{path} must not spend AI calls for anonymous users"


# ─── Admin review of close calls ──────────────────────────────────────────────

@pytest.fixture()
def tokens(db):
    """A throwaway student and admin, deleted afterwards."""
    import uuid

    from app.auth import create_access_token
    from app.models import User

    users = [User(username=f"tos_t_{role}_{uuid.uuid4().hex[:6]}", password_hash="x", role=role) for role in ("student", "admin")]
    db.add_all(users)
    db.commit()
    yield {u.role: {"Authorization": f"Bearer {create_access_token({'sub': u.username})}"} for u in users}
    for u in users:
        db.delete(u)
    db.commit()


def test_review_endpoints_are_admin_only(tokens):
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    q = {"exam_id": "pget", "paper_id": "mbbs-p1"}
    assert c.get("/api/tos/review", params=q).status_code == 401
    assert c.get("/api/tos/review", params=q, headers=tokens["student"]).status_code == 403
    assert c.post("/api/tos/review/1", json={**q, "action": "reject"}, headers=tokens["student"]).status_code == 403
    r = c.get("/api/tos/review", params=q, headers=tokens["admin"])
    assert r.status_code == 200 and "total" in r.json()


def test_review_decision_survives_a_retag(db, tokens):
    """Reassign a close call, re-run the tagger for that paper: the admin's topic must still stand."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import MCQTag
    from app.tos_tags import AXIS_MANUAL, AXIS_REVIEW, AXIS_TOPIC, tag_paper

    exam_id, paper_id = "mbbs", "y4-block-x"
    paper = tos_registry.get_paper(exam_id, paper_id)
    c = TestClient(app)
    items = c.get("/api/tos/review", params={"exam_id": exam_id, "paper_id": paper_id, "limit": 2},
                  headers=tokens["admin"]).json()["items"]
    if len(items) < 2:
        pytest.skip("no close calls on this paper")
    a, b = items
    other = next(t["id"] for s in paper["subjects"] for t in s["topics"] if t["id"] != a["suggested_topic_id"])
    try:
        assert c.post(f"/api/tos/review/{a['mcq_id']}", json={"exam_id": exam_id, "paper_id": paper_id,
                      "action": "reassign", "topic_id": other}, headers=tokens["admin"]).status_code == 200
        assert c.post(f"/api/tos/review/{b['mcq_id']}", json={"exam_id": exam_id, "paper_id": paper_id,
                      "action": "reject"}, headers=tokens["admin"]).status_code == 200
        tag_paper(db, exam_id, paper)   # a re-run replaces every automatic tag of the paper

        def tags(mid):
            return {(ax, lb) for ax, lb in db.query(MCQTag.axis, MCQTag.label).filter(
                MCQTag.mcq_id == mid, MCQTag.label.like(f"{exam_id}:{paper_id}/%"))}
        ta, tb = tags(a["mcq_id"]), tags(b["mcq_id"])
        assert (AXIS_TOPIC, topic_label(exam_id, other)) in ta and not any(ax == AXIS_REVIEW for ax, _ in ta)
        assert not any(ax in (AXIS_TOPIC, AXIS_REVIEW) for ax, _ in tb), "rejected question must stay out"
    finally:
        db.query(MCQTag).filter(MCQTag.axis == AXIS_MANUAL, MCQTag.mcq_id.in_([a["mcq_id"], b["mcq_id"]])).delete(
            synchronize_session=False)
        db.commit()
        tag_paper(db, exam_id, paper)   # back to the automatic tags
