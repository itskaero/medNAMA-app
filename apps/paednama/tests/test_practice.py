"""Practise: questions filed under a topic, a new set written on demand (generator faked), answered through medNAMA.

Run from the repo root:  .venv-gpu\\Scripts\\python.exe -m pytest apps/paednama/tests -q
"""

import uuid

import pytest

from apps.paednama.server import registry, settings  # noqa: F401  (backend/ on the path)

STAGE = "imm"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from apps.paednama.server.main import app

    return TestClient(app)


@pytest.fixture()
def student():
    from app.auth import create_access_token
    from app.database import SessionLocal
    from app.models import User

    db = SessionLocal()
    u = User(username=f"paeds_p_{uuid.uuid4().hex[:6]}", password_hash="x", role="student")
    db.add(u)
    db.commit()
    yield u, {"Authorization": f"Bearer {create_access_token({'sub': u.username})}"}
    db.delete(db.get(User, u.id))
    db.commit()
    db.close()


@pytest.fixture()
def topic_id():
    return registry.stage(STAGE)["syllabus"][3]["topics"][0]["id"]


@pytest.fixture()
def fake_generator(monkeypatch):
    """Two real MCQ rows stand in for a written set; the job reports them as done."""
    from app.database import SessionLocal
    from app.models import MCQ
    import app.quiz_generation as qg

    db = SessionLocal()
    made = [MCQ(question_text=f"Paeds practice test {uuid.uuid4().hex[:6]}?", options={"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"},
                correct_option="B", explanation_markdown="Because b.", status="ready", grounding="book") for _ in range(2)]
    db.add_all(made)
    db.commit()
    ids = [m.id for m in made]
    seen = {}

    def start(params):
        seen.update(params)
        return {"job_id": "job-test", "status": "running"}

    monkeypatch.setattr(qg, "start_quiz_job", start)
    monkeypatch.setattr(qg, "get_quiz_job", lambda db, job_id: {"job_id": job_id, "status": "done",
                                                                "result": {"mcqs": [{"id": i} for i in ids]}})
    monkeypatch.setattr("app.llm.llm_configured", lambda role="chat": True)
    yield ids, seen
    for i in ids:
        db.delete(db.get(MCQ, i))
    db.commit()
    db.close()


def test_new_set_is_scoped_filed_and_answered(client, student, topic_id, fake_generator):
    _, h = student
    ids, seen = fake_generator
    r = client.post("/api/paeds/practice/start", json={"stage": STAGE, "topic_id": topic_id}, headers=h)
    assert r.status_code == 200, r.text
    assert seen["level"] == "FCPS-II" and seen["mcq_count"] == 10 and seen["book_ids"]
    from app.database import SessionLocal
    from apps.paednama.server.routes import _ready_books
    db = SessionLocal()
    mcq_books = {b["id"] for b in _ready_books(db) if b["role"] == "mcq"}
    db.close()
    assert not mcq_books & set(seen["book_ids"]), "never written from the MCQ critique books"

    r = client.get(f"/api/paeds/practice/job/job-test?stage={STAGE}&topic_id={topic_id}", headers=h)
    assert r.json() == {"status": "done", "added": 2}
    r = client.get(f"/api/paeds/practice/job/job-test?stage={STAGE}&topic_id={topic_id}", headers=h)
    assert r.json()["added"] == 0, "polling again files nothing twice"

    q = client.get(f"/api/paeds/practice?stage={STAGE}&topic_id={topic_id}", headers=h).json()
    assert [x["id"] for x in q["questions"]][:2] == ids and q["answered"] == 0

    a = client.post("/api/study/answer", json={"mcq_id": ids[0], "selected_option": "A", "confidence": "sure"}, headers=h)
    assert a.status_code == 200 and a.json()["is_correct"] is False
    q = client.get(f"/api/paeds/practice?stage={STAGE}&topic_id={topic_id}", headers=h).json()
    assert q["answered"] == 1 and q["correct"] == 0
    assert q["questions"][-1]["id"] == ids[0], "answered questions go to the end"


def test_unknown_topic(client, student):
    _, h = student
    assert client.get(f"/api/paeds/practice?stage={STAGE}&topic_id=nope", headers=h).status_code == 404
