"""Dr. Shifa: explain/tutor through medNAMA's generator scoped to the Paediatrics books, and the clinical-reasoning
coach (fake LLM, fake passages, fake generator: no AI cost, no models loaded)."""

import uuid
from types import SimpleNamespace

import pytest

from apps.paednama.server import settings, shifa, study  # noqa: F401


FEVER = ("Fever without a focus in infants younger than three months needs a full sepsis evaluation, because "
         "serious bacterial infection is common and the signs are subtle. Urinary tract infection is the most "
         "frequent cause; meningitis and bacteraemia must not be missed.")


@pytest.fixture()
def env(monkeypatch):
    from app.auth import create_access_token
    from app.database import SessionLocal
    from app.models import User

    calls = SimpleNamespace(generator=[], llm=[])

    def fake_generate(session, query, history=None, level=None, mode=None, book_ids=None, **kw):
        calls.generator.append({"query": query, "history": history, "level": level, "mode": mode, "book_ids": book_ids})
        return {"answer_markdown": "Kernicterus risk rises with bilirubin [Nelson, Page 210].", "grounding": "textbook",
                "citations": [{"book_title": "Nelson", "page_number": 210, "excerpt": "x"}]}

    def fake_llm(messages, json_mode=False, role="chat", label=""):
        calls.llm.append({"label": label, "messages": messages})
        return "What are your top three diagnoses [1]? Not this [9]."

    book = SimpleNamespace(title="Pediatric Decision Making Strategies")
    monkeypatch.setattr("app.generation.generate_answer", fake_generate)
    monkeypatch.setattr(study, "LLM", fake_llm)
    monkeypatch.setattr(study, "retrieve", lambda db, q, ids, limit=8: [SimpleNamespace(
        id=1, book_id=ids[0], book=book, chapter="Fever", page_number=44, content=FEVER)])

    db = SessionLocal()
    users = [User(username=f"shifa_t_{uuid.uuid4().hex[:6]}", password_hash="x", role="student") for _ in range(2)]
    db.add_all(users)
    db.commit()
    yield SimpleNamespace(db=db, calls=calls, h=[{"Authorization": f"Bearer {create_access_token({'sub': u.username})}"}
                                                 for u in users])
    for u in users:
        db.delete(db.get(User, u.id))
    db.commit()
    db.close()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from apps.paednama.server.main import app

    return TestClient(app)


def test_explain_and_tutor_use_the_paediatrics_books(client, env):
    from apps.paednama.server.routes import _ready_books

    paeds_ids = sorted(b["id"] for b in _ready_books(env.db))
    r = client.post("/api/paeds/shifa/ask", headers=env.h[0], json={"text": "Neonatal jaundice thresholds?", "mode": "explain"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["message"]["role"] == "shifa" and body["message"]["citations"][0]["page"] == 210
    call = env.calls.generator[-1]
    assert sorted(call["book_ids"]) == paeds_ids and call["mode"] is None and call["level"] == "FCPS-II"
    r2 = client.post("/api/paeds/shifa/ask", headers=env.h[0], json={
        "text": "Teach me", "mode": "tutor", "chat_id": body["chat_id"]}).json()
    assert r2["chat_id"] == body["chat_id"] and env.calls.generator[-1]["mode"] == "tutor"
    assert [m["role"] for m in env.calls.generator[-1]["history"]] == ["user", "assistant"], "history carried over"


def test_reasoning_coach_cites_only_real_passages(client, env):
    r = client.post("/api/paeds/shifa/ask", headers=env.h[0], json={
        "text": "A 6-week-old with fever 38.5 and no focus", "mode": "reasoning"}).json()
    msg = r["message"]
    assert "[9]" not in msg["content"] and [c["n"] for c in msg["citations"]] == [1]
    system = env.calls.llm[-1]["messages"][0]["content"]
    assert "Dr. Shifa" in system and "do not give your answer" in system


def test_chats_are_private_and_deletable(client, env):
    cid = client.post("/api/paeds/shifa/ask", headers=env.h[0], json={"text": "Rickets?", "mode": "explain"}).json()["chat_id"]
    assert client.get(f"/api/paeds/shifa/chats/{cid}", headers=env.h[1]).status_code == 404
    assert client.post("/api/paeds/shifa/ask", headers=env.h[1], json={"text": "x", "chat_id": cid}).status_code == 404
    detail = client.get(f"/api/paeds/shifa/chats/{cid}", headers=env.h[0]).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "shifa"]
    assert any(c["id"] == cid for c in client.get("/api/paeds/shifa/chats", headers=env.h[0]).json()["chats"])
    assert client.delete(f"/api/paeds/shifa/chats/{cid}", headers=env.h[0]).json()["ok"]
    assert client.post("/api/paeds/shifa/ask", headers=env.h[0], json={"text": "  ", "mode": "explain"}).status_code == 422
