"""Phase C: topic one-pagers, the book lens and the reading loop, with a fake LLM and fake passages (no AI cost,
no models loaded). Missed answers create a real medNAMA concept card in the dev database, removed afterwards.

Run from the repo root:  .venv-gpu\\Scripts\\python.exe -m pytest apps/paednama/tests -q
"""

import json
import uuid
from types import SimpleNamespace

import numpy as np
import pytest

from apps.paednama.server import registry, settings, study  # noqa: F401

STAGE = "imm"
TOPIC = next(t for s in registry.stage(STAGE)["syllabus"] if s["system"] == "NEONATOLOGY"
             for t in s["topics"] if t["name"] == "Jaundice neonatorum")["id"]

PASSAGE_A = ("Jaundice appearing in the first 24 hours of life is always pathological. Phototherapy is started "
             "according to hour-specific bilirubin nomograms. The causes include haemolysis from blood group "
             "incompatibility, red cell membrane defects and enzyme deficiencies, which need prompt evaluation.")
PASSAGE_B = ("Exchange transfusion is indicated when bilirubin rises despite intensive phototherapy. It removes "
             "bilirubin and antibody-coated red cells, and is reserved for infants at risk of acute bilirubin "
             "encephalopathy, whose early signs include lethargy, hypotonia and poor feeding.")


def fake_chunks(book_ids, n=2):
    book = SimpleNamespace(title="Nelson Essentials Peds 8e")
    texts = [PASSAGE_A, PASSAGE_B][:n]
    return [SimpleNamespace(id=None, book_id=book_ids[0], book=book, chapter="Neonatal jaundice", page_number=210 + i,
                            content=t) for i, t in enumerate(texts)]


class FakeLLM:
    def __init__(self):
        self.calls = []

    def __call__(self, messages, json_mode=False, role="chat", label=""):
        self.calls.append(label)
        if label == "paeds-sheet-queries":
            return json.dumps({"queries": ["neonatal jaundice causes", "jaundice examination", "bilirubin tests",
                                           "phototherapy exchange transfusion"]})
        if label == "paeds-sheet":
            return "### In one line\nEarly jaundice is pathological [1]. Exchange if rising [2]. Made up [7]."
        if label == "paeds-pack":
            return json.dumps({
                "case": {"stem": "A 20-hour-old term baby is yellow.", "question": "Is this physiological?",
                         "answer": "No: jaundice in the first 24 hours is pathological.", "section": 1},
                "questions": [
                    {"section": 1, "q": "Jaundice in the first 24 hours is?", "answer": "Always pathological",
                     "quote": "Jaundice appearing in the first 24 hours of life is always pathological."},
                    {"section": 1, "q": "Invented?", "answer": "x", "quote": "This sentence is not in the book."},
                    {"section": 2, "q": "When is exchange transfusion indicated?",
                     "answer": "Bilirubin rising despite intensive phototherapy",
                     "quote": "Exchange transfusion is indicated when bilirubin rises despite intensive phototherapy."},
                ]})
        if label == "paeds-grade":
            answer = messages[-1]["content"].split("TRAINEE'S ANSWER:")[-1].lower()
            ok = "patholog" in answer or "phototherapy" in answer
            return json.dumps({"correct": ok, "feedback": "Right." if ok else "It is always pathological."})
        if label == "paeds-explain":
            return json.dumps({"score": 72, "points_hit": ["early = pathological"], "points_missed": ["nomograms"],
                               "errors": [], "feedback": "Good."})
        raise AssertionError(label)


@pytest.fixture()
def env(monkeypatch):
    from app.auth import create_access_token
    from app.database import SessionLocal
    from app.models import ConceptCard, ConceptReview, User
    from apps.paednama.server.models import PaedsReading, PaedsReadPack, PaedsSheet

    fake = FakeLLM()
    monkeypatch.setattr(study, "LLM", fake)
    monkeypatch.setattr(study, "retrieve", lambda db, q, ids, limit=8: fake_chunks(ids))
    rng = np.random.default_rng(7)
    vec = rng.normal(size=1024)
    monkeypatch.setattr("app.retention._embed", lambda texts: [vec / np.linalg.norm(vec)])
    monkeypatch.setattr("app.retention._find_existing_card", lambda db, v: None)

    db = SessionLocal()
    for model in (PaedsSheet, PaedsReadPack):
        db.query(model).filter_by(stage=STAGE, topic_id=TOPIC).delete()
    db.commit()
    u = User(username=f"paeds_s_{uuid.uuid4().hex[:6]}", password_hash="x", role="student")
    db.add(u)
    db.commit()
    yield SimpleNamespace(db=db, user=u, llm=fake, h={"Authorization": f"Bearer {create_access_token({'sub': u.username})}"})
    db.rollback()
    cards = [r.concept_id for r in db.query(ConceptReview).filter_by(user_id=u.id).all()]
    db.query(PaedsReading).filter_by(user_id=u.id).delete()
    db.query(ConceptReview).filter_by(user_id=u.id).delete()
    if cards:
        db.query(ConceptCard).filter(ConceptCard.id.in_(cards)).delete(synchronize_session=False)
    for model in (PaedsSheet, PaedsReadPack):
        db.query(model).filter_by(stage=STAGE, topic_id=TOPIC).delete()
    db.delete(db.get(User, u.id))
    db.commit()
    db.close()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from apps.paednama.server.main import app

    return TestClient(app)


def test_citations_point_at_real_passages():
    md, cites = study.keep_valid_citations("a [1] b [2, 9] c [9]", [{"n": 1}, {"n": 2}, {"n": 3}])
    assert md == "a [1] b [2] c " and [c["n"] for c in cites] == [1, 2]


def test_sheet_written_once_and_lens_scoped(client, env):
    r = client.get(f"/api/paeds/topic/sheet?stage={STAGE}&topic_id={TOPIC}", headers=env.h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "[7]" not in body["markdown"] and {c["n"] for c in body["citations"]} == {1, 2}
    again = client.get(f"/api/paeds/topic/sheet?stage={STAGE}&topic_id={TOPIC}", headers=env.h).json()
    assert again["cached"] and env.llm.calls.count("paeds-sheet") == 1
    books = client.get(f"/api/paeds/topic?stage={STAGE}&topic_id={TOPIC}", headers=env.h).json()["lenses"]
    one = next(b for b in books if b["id"] != "all")
    lensed = client.get(f"/api/paeds/topic/sheet?stage={STAGE}&topic_id={TOPIC}&lens={one['id']}", headers=env.h).json()
    assert all(c["book_id"] == int(one["id"]) for c in lensed["citations"])
    assert client.get(f"/api/paeds/topic/sheet?stage={STAGE}&topic_id={TOPIC}&lens=999999", headers=env.h).status_code == 422


def test_reading_loop(client, env):
    q = f"stage={STAGE}&topic_id={TOPIC}"
    pack = client.get(f"/api/paeds/read?{q}", headers=env.h).json()
    assert pack["case"]["question"] and "answer" not in pack["case"]
    assert pack["sections"][0]["questions"] == ["Jaundice in the first 24 hours is?"], "unverified quote dropped"
    assert len(pack["sections"][1]["questions"]) == 1
    case = client.post("/api/paeds/read/case", headers=env.h, json={"stage": STAGE, "topic_id": TOPIC, "answer": "pathological"}).json()
    assert "pathological" in case["answer"]

    right = client.post("/api/paeds/read/answer", headers=env.h, json={
        "stage": STAGE, "topic_id": TOPIC, "section": 0, "index": 0, "answer": "pathological", "confidence": "sure"}).json()
    assert right["correct"] and not right["card_added"] and right["quote"].startswith("Jaundice appearing")
    wrong = client.post("/api/paeds/read/answer", headers=env.h, json={
        "stage": STAGE, "topic_id": TOPIC, "section": 1, "index": 0, "answer": "never", "confidence": "sure"}).json()
    assert not wrong["correct"] and wrong["card_added"]

    from app.models import ConceptCard, ConceptReview

    rev = env.db.query(ConceptReview).filter_by(user_id=env.user.id).one()
    card = env.db.get(ConceptCard, rev.concept_id)
    assert card.subject == "Paediatrics" and card.quote.startswith("Exchange transfusion") and rev.lapses == 1

    assert client.post("/api/paeds/read/explain", headers=env.h, json={
        "stage": STAGE, "topic_id": TOPIC, "section": 0, "text": "too short"}).status_code == 422
    for s in (0, 1):
        ex = client.post("/api/paeds/read/explain", headers=env.h, json={
            "stage": STAGE, "topic_id": TOPIC, "section": s, "text": "Early jaundice in the first day is pathological."}).json()
    assert ex["score"] == 72 and ex["finished"]
    prog = client.get(f"/api/paeds/progress?stage={STAGE}", headers=env.h).json()
    assert prog["finished"] == 1 and prog["topics"][TOPIC]["recall"] == [1, 2] and prog["topics"][TOPIC]["cards_added"] == 1
    assert env.llm.calls.count("paeds-pack") == 1, "the pack is prepared once"


def test_out_of_range_and_auth(client, env):
    assert client.get(f"/api/paeds/read?stage={STAGE}&topic_id={TOPIC}").status_code == 401
    assert client.post("/api/paeds/read/answer", headers=env.h, json={
        "stage": STAGE, "topic_id": TOPIC, "section": 9, "index": 0, "answer": "x"}).status_code == 422
    assert client.get(f"/api/paeds/topic?stage={STAGE}&topic_id=nope", headers=env.h).status_code == 404


def test_old_notes_are_served_and_upgraded_only_by_the_batch(client, env):
    from apps.paednama.server.models import PaedsSheet

    env.db.add(PaedsSheet(stage=STAGE, topic_id=TOPIC, lens="all", markdown="old one-pager", citations=[], version=1))
    env.db.commit()
    body = client.get(f"/api/paeds/topic/sheet?stage={STAGE}&topic_id={TOPIC}", headers=env.h).json()
    assert body["markdown"] == "old one-pager" and body["cached"], "opening a topic never waits on a rewrite"
    assert "paeds-sheet" not in env.llm.calls
    # the batch (scripts/prewrite_sheets.py) upgrades it
    from apps.paednama.server.routes import _ready_books

    books = _ready_books(env.db)
    out = study.get_sheet(env.db, STAGE, TOPIC, "all", [b["id"] for b in books], {b["id"]: b["title"] for b in books},
                          upgrade=True)
    assert out["markdown"] != "old one-pager"
    env.db.expire_all()
    assert env.db.query(PaedsSheet).filter_by(stage=STAGE, topic_id=TOPIC, lens="all").one().version == study.SHEET_VERSION
