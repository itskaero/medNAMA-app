"""PaedNAMA server: the curriculum registry, the profile and the book roles.

Run from the repo root:  .venv-gpu\\Scripts\\python.exe -m pytest apps/paednama/tests -q
Uses the dev database; makes throwaway users and removes them afterwards.
"""

import uuid
from datetime import date, timedelta

import pytest

from apps.paednama.server import registry, settings  # noqa: F401  (backend/ on the path)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from apps.paednama.server.main import app

    return TestClient(app)


@pytest.fixture()
def users():
    from app.auth import create_access_token
    from app.database import SessionLocal
    from app.models import User

    db = SessionLocal()
    made = [User(username=f"paeds_t_{r}_{uuid.uuid4().hex[:6]}", password_hash="x", role=r) for r in ("student", "admin")]
    db.add_all(made)
    db.commit()
    yield {u.role: (u, {"Authorization": f"Bearer {create_access_token({'sub': u.username})}"}) for u in made}
    for u in made:
        db.delete(db.get(User, u.id))
    db.commit()
    db.close()


def test_registry_carries_the_2026_exam_facts():
    imm, f2 = registry.stage("imm"), registry.stage("fcps2")
    assert [p["questions"] for p in imm["theory"]["papers"]] == [100, 100]
    assert imm["theory"]["page"] == 67 and imm["clinical"][0]["stations"] == "12 to 18"
    assert imm["clinical"][0]["all_interactive"] is True
    comps = [c["component"] for c in f2["clinical"]]
    assert comps == ["TOACS", "Long case", "Short cases"]
    long_case = f2["clinical"][1]
    assert long_case["minutes"] == 60
    assert "Has knowledge of recent advances relevant to the case" in long_case["assessed_on"]["Case presentation / discussion"]


def test_syllabus_parsed_with_pages_and_unique_ids():
    for sid in registry.STAGES:
        st = registry.stage(sid)
        assert len(st["syllabus"]) >= 25
        ids = [t["id"] for s in st["syllabus"] for t in s["topics"]]
        assert len(ids) == len(set(ids)) and len(ids) > 400
        assert all(s["page"] for s in st["syllabus"])
        assert all(s["topics"] for s in st["syllabus"]), "every system has at least one topic"
        assert len(st["mini_cex"]["topics"]) >= 35 and len(st["dops"]["procedures"]) >= 25
    neo = next(s for s in registry.stage("imm")["syllabus"] if s["system"] == "NEONATOLOGY")
    names = [t["name"] for t in neo["topics"]]
    assert "Neonatal sepsis (early and late onset)" in names and "Jaundice neonatorum" in names


def test_search_and_find():
    hits = registry.search("imm", "neonatal sepsis")
    assert hits and hits[0]["system"] == "NEONATOLOGY"
    system, topic = registry.find_topic("imm", hits[0]["id"])
    assert topic["id"] == hits[0]["id"]


def test_endpoints_need_sign_in(client):
    assert client.get("/api/paeds/exams").status_code == 401
    assert client.get("/api/paeds/profile").status_code == 401


def test_profile_round_trip(client, users):
    _, h = users["student"]
    p = client.get("/api/paeds/profile", headers=h).json()
    assert p["needs_setup"] and {s["id"] for s in p["stages"]} == {"imm", "fcps2"}
    when = (date.today() + timedelta(days=120)).isoformat()
    r = client.put("/api/paeds/profile", headers=h, json={"stage": "imm", "exam_date": when, "training_year": 2,
                                                          "rotation": "Neonatology", "minutes_per_day": 90})
    assert r.status_code == 200 and r.json()["rotation"] == "Neonatology"
    assert client.get("/api/paeds/profile", headers=h).json()["needs_setup"] is False
    assert client.put("/api/paeds/profile", headers=h, json={"stage": "mbbs"}).status_code == 422
    past = (date.today() - timedelta(days=1)).isoformat()
    assert client.put("/api/paeds/profile", headers=h, json={"stage": "imm", "exam_date": past}).status_code == 422


def test_syllabus_and_exam_endpoints(client, users):
    _, h = users["student"]
    ex = client.get("/api/paeds/exams", headers=h).json()
    assert {s["id"] for s in ex["stages"]} == {"imm", "fcps2"}
    syl = client.get("/api/paeds/syllabus?stage=fcps2", headers=h).json()
    assert syl["systems"] and syl["mini_cex"]["topics"]
    assert client.get("/api/paeds/syllabus?stage=nope", headers=h).status_code == 404


def test_book_roles(client, users):
    from apps.paednama.server.routes import default_role

    assert default_role("Nelson Essentials of Pediatrics", "") == "spine"
    assert default_role("Nelson Textbook of Pediatrics", "nelson-ed22-vol1.pdf") == "reference"
    assert default_role("Cloherty and Stark's Manual of Neonatal Care", "") == "neonatal"
    assert default_role("Davidson's Principles of Medicine", "davidson-medicine.pdf") is None
    _, hs = users["student"]
    _, ha = users["admin"]
    books = client.get("/api/paeds/books", headers=hs).json()["books"]
    assert any("nelson" in (b["title"] or "").lower() for b in books)
    assert all("davidson" not in (b["title"] or "").lower() for b in books)
    bid = books[0]["id"]
    assert client.put(f"/api/paeds/books/{bid}/role", headers=hs, json={"role": "spine"}).status_code == 403
    assert client.put(f"/api/paeds/books/{bid}/role", headers=ha, json={"role": "bogus"}).status_code == 422
