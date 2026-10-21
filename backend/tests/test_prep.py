"""Onboarding profile, today's plan and anonymous usage counts (app/routers/prep.py).

Run from the repo root:  .venv-gpu\\Scripts\\python.exe -m pytest backend/tests/test_prep.py -q
Uses the dev database; creates throwaway users and removes them (and its usage rows) afterwards.
"""

import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module")
def db():
    from sqlalchemy import text

    from app.database import SessionLocal

    s = SessionLocal()
    try:
        s.execute(text("select prep_profile from users limit 1"))
    except Exception as e:
        pytest.skip(f"run alembic upgrade head first: {e}")
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def users(db):
    from app.auth import create_access_token
    from app.models import User

    made = [User(username=f"prep_t_{r}_{uuid.uuid4().hex[:6]}", password_hash="x", role=r) for r in ("student", "admin")]
    db.add_all(made)
    db.commit()
    yield {u.role: (u, {"Authorization": f"Bearer {create_access_token({'sub': u.username})}"}) for u in made}
    db.rollback()
    for u in made:
        db.delete(db.get(User, u.id))
    db.commit()


def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_new_user_needs_onboarding_then_profile_shapes_level(users):
    c = client()
    _, h = users["student"]
    p = c.get("/api/prep/profile", headers=h).json()
    assert p["needs_onboarding"] and not p["onboarded"]
    assert any(e["id"] == "pget" for e in p["options"])
    when = (date.today() + timedelta(days=70)).isoformat()
    r = c.put("/api/prep/profile", json={"exam_id": "pget", "paper_id": "p2-medicine-allied", "exam_date": when,
                                        "minutes_per_day": 60}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["exam_level"] == "PGET" and body["onboarded"] and body["clinical"] is True
    assert body["paper_title"] and body["exam_date"] == when


def test_profile_validation(users):
    c = client()
    _, h = users["student"]
    assert c.put("/api/prep/profile", json={"exam_id": "pget", "paper_id": "nope"}, headers=h).status_code == 422
    assert c.put("/api/prep/profile", json={"exam_id": None, "exam_level": None}, headers=h).status_code == 422
    past = (date.today() - timedelta(days=1)).isoformat()
    assert c.put("/api/prep/profile", json={"exam_id": "pget", "paper_id": "mbbs-p1", "exam_date": past},
                 headers=h).status_code == 422
    r = c.put("/api/prep/profile", json={"exam_level": "FCPS-II"}, headers=h).json()
    assert r["exam_level"] == "FCPS-II" and r["clinical"] and r["paper_id"] is None


def test_existing_level_only_needs_paper(db, users):
    from app.models import User

    u, h = users["student"]
    db.get(User, u.id).exam_level = "PGET"
    db.commit()
    p = client().get("/api/prep/profile", headers=h).json()
    assert p["needs_paper"] and not p["needs_onboarding"]


def test_plan_suggests_a_topic_and_weekly_target(users):
    c = client()
    _, h = users["student"]
    when = (date.today() + timedelta(days=28)).isoformat()
    c.put("/api/prep/profile", json={"exam_id": "pget", "paper_id": "mbbs-p1", "exam_date": when, "minutes_per_day": 120},
          headers=h)
    plan = c.get("/api/today/plan", headers=h).json()
    assert plan["days_left"] == 28 and plan["topics_total"] > 0
    assert plan["weekly_target"] == -(-plan["topics_total"] // 4)       # all topics left, 4 weeks
    assert plan["primary"]["kind"] == "topic" and plan["primary"]["params"]["topic"]
    assert 1 <= len(plan["today_topics"]) <= 3


def test_plan_without_profile_asks_for_setup(users):
    plan = client().get("/api/today/plan", headers=users["student"][1]).json()
    assert plan["primary"]["kind"] == "setup"


def test_usage_counts_are_anonymous_and_admin_only(db, users):
    from sqlalchemy import text

    c = client()
    _, hs = users["student"]
    _, ha = users["admin"]
    before = db.execute(text("select coalesce(sum(n),0) from usage_counts where view='lookalikes' and day=current_date")).scalar()
    assert c.post("/api/usage", json={"view": "lookalikes"}, headers=hs).json()["ok"]
    assert not c.post("/api/usage", json={"view": "drop table"}, headers=hs).json()["ok"]
    after = db.execute(text("select coalesce(sum(n),0) from usage_counts where view='lookalikes' and day=current_date")).scalar()
    assert after == before + 1
    cols = {r[0] for r in db.execute(text("select column_name from information_schema.columns where table_name='usage_counts'"))}
    assert cols == {"view", "day", "n"}, "no user column: counts stay anonymous"
    assert c.get("/api/admin/usage", headers=hs).status_code == 403
    views = c.get("/api/admin/usage", headers=ha).json()["views"]
    assert any(v["view"] == "lookalikes" for v in views)
    db.execute(text("update usage_counts set n = n - 1 where view='lookalikes' and day=current_date"))
    db.commit()


def test_plan_only_suggests_topics_with_something_to_study(db, users):
    """Paediatrics has no bank questions and some topics have no textbook passages: never send the learner there."""
    from sqlalchemy import text

    from app.tos_sheets import scope_key

    c = client()
    _, h = users["student"]
    c.put("/api/prep/profile", json={"exam_id": "pget", "paper_id": "p2-paediatric-medicine", "minutes_per_day": 120},
          headers=h)
    plan = c.get("/api/today/plan", headers=h).json()
    for t in plan["today_topics"]:
        tid = t["params"]["topic"]
        sheets = db.execute(text("select count(*) from topic_summaries where scope_key = any(:k)"), {"k": [
            scope_key(k, "pget", "p2-paediatric-medicine", tid) for k in ("refresher", "notes")]}).scalar()
        mcqs = db.execute(text("select count(*) from mcq_tags where axis='tos_topic' and label=:l"),
                          {"l": f"pget:{tid}"}).scalar()
        assert sheets or mcqs, f"{t['topic']} has nothing to study"
