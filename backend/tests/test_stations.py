"""TOACS / viva stations: drafting checks, review and publication, the examiner, scoring safeguards, debrief,
challenges, calibration and data deletion.

Run from the repo root:  .venv-gpu\\Scripts\\python.exe -m pytest backend/tests/test_stations.py -q

Uses the dev database (DATABASE_URL) and cleans up everything it creates. The AI is a scripted stand-in
(monkeypatched app.stations._default_llm), so no AI calls are made.
"""

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import stations as S  # noqa: E402
from app.station_models import (Station, StationAttempt, StationAudit, StationChallenge, StationCriterion,  # noqa: E402
                                StationGold, StationScore, StationVersion)

PASSAGES = [
    {"n": 1, "book_title": "Nelson Pediatrics 22e Vol 1", "page_number": 100, "chapter": "Neonatal jaundice",
     "content": "Jaundice appearing within the first 24 hours of life is always pathological and requires urgent "
                "measurement of total serum bilirubin. Phototherapy is started according to hour-specific thresholds."},
    {"n": 2, "book_title": "Nelson Pediatrics 22e Vol 1", "page_number": 101, "chapter": "Neonatal jaundice",
     "content": "Exchange transfusion is indicated when bilirubin rises despite intensive phototherapy or when there "
                "are signs of acute bilirubin encephalopathy such as hypertonia, arching and a high-pitched cry."},
    {"n": 3, "book_title": "Nelson Pediatrics 22e Vol 1", "page_number": 102, "chapter": "Neonatal jaundice",
     "content": "A direct Coombs test identifies immune haemolysis from Rh or ABO incompatibility."},
]

CONTENT = {
    "title": "Test station: early neonatal jaundice", "station_type": "viva",
    "scenario": "A 20-hour-old term baby is noticed to be yellow by the midwife.",
    "stages": [{"after_item": 0, "text": "Total serum bilirubin is 210 umol/L."}],
    "instructions_timed": "Answer the examiner's questions.",
    "instructions_guided": "Answer the examiner's questions. Ask for a hint if stuck.",
    "framework": [
        {"id": 0, "prompt": "What is your first concern?", "competency": "reasoning", "follow_ups": ["Why at this age?"]},
        {"id": 1, "prompt": "What will you do now?", "competency": "prioritisation", "follow_ups": []},
        {"id": 2, "prompt": "When would you escalate treatment?", "competency": "safety", "follow_ups": []},
    ],
    "criteria": [
        {"competency": "reasoning", "expected": "States jaundice under 24 hours is pathological", "marks": 2,
         "alternatives": ["early jaundice is abnormal"], "safety_critical": False},
        {"competency": "prioritisation", "expected": "Measures total serum bilirubin urgently", "marks": 2,
         "alternatives": ["checks serum bilirubin"], "safety_critical": False},
        {"competency": "safety", "expected": "Escalates to exchange transfusion for encephalopathy signs", "marks": 2,
         "alternatives": [], "safety_critical": True},
        {"competency": "knowledge", "expected": "Requests a direct Coombs test", "marks": 1,
         "alternatives": ["DAT"], "safety_critical": False},
    ],
    "model_answer": "Pathological jaundice; urgent TSB; phototherapy; Coombs; exchange if encephalopathy.",
    "time_limit_s": 300, "difficulty": 3,
}


class FakeLLM:
    """Scripted stand-in for the AI, keyed by the call's label."""

    def __init__(self):
        self.turn = {"action": "next", "follow_up_index": None, "teaching": "", "hint": ""}
        self.scores = None      # callable(criteria) -> {"scores": [...]}
        self.draft = None
        self.calls = []

    def __call__(self, messages, **kw):
        label = kw.get("label")
        self.calls.append(label)
        if label == "station-turn":
            return dict(self.turn)
        if label == "station-score":
            return self.scores()
        if label == "station-draft":
            return self.draft
        raise AssertionError(f"unexpected AI call {label}")


@pytest.fixture(scope="module")
def db():
    from sqlalchemy import text

    from app.database import SessionLocal

    s = SessionLocal()
    try:
        s.execute(text("select 1 from stations limit 1"))
    except Exception as e:
        pytest.skip(f"stations tables unavailable (run alembic upgrade head): {e}")
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def fake(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(S, "_default_llm", f)
    return f


@pytest.fixture()
def users(db):
    from app.auth import create_access_token
    from app.models import AnswerEvent, User

    made = [User(username=f"st_t_{role}_{uuid.uuid4().hex[:6]}", password_hash="x", role=role)
            for role in ("student", "admin")]
    db.add_all(made)
    db.commit()
    out = {u.role: (u, {"Authorization": f"Bearer {create_access_token({'sub': u.username})}"}) for u in made}
    yield out
    db.rollback()
    for u in made:
        db.query(StationChallenge).filter(StationChallenge.user_id == u.id).delete()
        db.query(StationAttempt).filter(StationAttempt.user_id == u.id).delete()
        db.query(AnswerEvent).filter(AnswerEvent.user_id == u.id).delete()
        db.delete(db.get(User, u.id))
    db.commit()


@pytest.fixture()
def station(db):
    """A published, approved station with the CONTENT above."""
    st = Station(specialty="Paediatrics", tos_exam_id="pget", tos_paper_id="p2-paediatric-medicine",
                 tos_topic_id=None, title=CONTENT["title"], status="draft", provenance="authored", created_by="test")
    db.add(st)
    db.flush()
    v = S._new_version(db, st, CONTENT, "test")
    st.current_version_id = v.id
    db.commit()
    S.approve_version(db, st, v, "test")
    S.publish(db, st, "test")
    yield st
    db.rollback()
    ids = [str(st.id), *[str(x.id) for x in db.query(StationVersion).filter(StationVersion.station_id == st.id)]]
    db.query(StationAudit).filter(StationAudit.entity_id.in_(ids)).delete(synchronize_session=False)
    db.query(Station).filter(Station.id == st.id).update({"current_version_id": None})
    db.commit()
    db.delete(db.get(Station, st.id))
    db.commit()


def criteria(db, st):
    return S.criteria_of(db, st.current_version_id)


def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


# ─── Pure checks ──────────────────────────────────────────────────────────────

def test_quote_in_requires_real_text():
    assert S.quote_in("always pathological and requires urgent", PASSAGES[0]["content"])
    assert not S.quote_in("always physiological", PASSAGES[0]["content"])
    assert not S.quote_in("short", PASSAGES[0]["content"]), "tiny quotes prove nothing"


def test_validate_draft_flags_unquoted_criteria():
    data = {**CONTENT, "criteria": [
        {**CONTENT["criteria"][0], "source": {"n": 1, "quote": "within the first 24 hours of life is always pathological"}},
        {**CONTENT["criteria"][1], "source": {"n": 1, "quote": "a sentence that is not in the passage at all"}},
        {**CONTENT["criteria"][2], "source": {"n": 2, "quote": "signs of acute bilirubin encephalopathy such as hypertonia"}},
        {**CONTENT["criteria"][3], "source": {"n": 9, "quote": "direct Coombs test identifies immune haemolysis"}},
    ]}
    clean, problems = S.validate_draft(data, PASSAGES)
    assert [c["flagged"] for c in clean["criteria"]] == [False, True, False, True]
    assert clean["criteria"][2]["safety_critical"] is True
    assert not problems


def test_calibration_metrics():
    c_safe = StationCriterion(id=1, marks=2, safety_critical=True)
    c_norm = StationCriterion(id=2, marks=2, safety_critical=False)
    m = S.calibration_metrics([(c_safe, 0, 2, False), (c_safe, 2, 2, True), (c_norm, 2, 1.5, False), (c_norm, 1, 1, False)])
    assert m["safety_misses"] == 1          # expert deducted, AI gave full marks
    assert m["false_flags"] == 1            # AI said unsafe where expert gave full marks
    assert m["agreement"] == 0.75 and m["mae"] == round((1 + 0 + 0.25 + 0) / 4, 3)


# ─── Drafting ─────────────────────────────────────────────────────────────────

def test_draft_station_is_saved_as_unpublished_draft(db, fake, monkeypatch):
    monkeypatch.setattr(S, "_passages", lambda db, q, books, limit=8: PASSAGES)
    monkeypatch.setattr(S, "specialty_books", lambda db, sp: [1])
    fake.draft = {**CONTENT, "criteria": [
        {**c, "source": {"n": 1, "quote": "Jaundice appearing within the first 24 hours of life is always pathological"}}
        for c in CONTENT["criteria"]]}
    from app.tos import tos_registry

    paper = tos_registry.get_paper("pget", "p2-paediatric-medicine")
    topic_id = paper["subjects"][0]["topics"][0]["id"]
    st = S.draft_station(db, "tester", "pget", "p2-paediatric-medicine", topic_id, "Paediatrics")
    try:
        assert st.status == "draft" and st.provenance == "ai_draft"
        v = db.get(StationVersion, st.current_version_id)
        assert v.approved_at is None and len(S.criteria_of(db, v.id)) == 4
        with pytest.raises(ValueError):
            S.publish(db, st, "tester")     # nothing approved yet
    finally:
        db.query(StationAudit).filter(StationAudit.entity_id == str(st.id)).delete()
        db.query(Station).filter(Station.id == st.id).update({"current_version_id": None})
        db.commit()
        db.delete(st)
        db.commit()


# ─── Review, publication, visibility ──────────────────────────────────────────

def test_learners_see_only_published(db, station, users):
    c = client()
    _, h = users["student"]
    ids = [s["id"] for s in c.get("/api/stations", headers=h).json()["stations"]]
    assert station.id in ids
    S.set_status(db, station, "approved", "test")             # unpublish
    assert station.id not in [s["id"] for s in c.get("/api/stations", headers=h).json()["stations"]]
    assert c.get(f"/api/stations/{station.id}", headers=h).status_code == 404
    assert c.post(f"/api/stations/{station.id}/attempts", json={"mode": "guided"}, headers=h).status_code == 404


def test_station_detail_never_shows_answers(db, station, users):
    _, h = users["student"]
    body = client().get(f"/api/stations/{station.id}", headers=h).json()
    text = str(body)
    for secret in ("model_answer", "criteria", "framework", "Exchange", "Coombs"):
        assert secret not in text


def test_edit_makes_new_version_and_history_is_pinned(db, station, users, fake):
    user, _ = users["student"]
    v1 = station.current_version_id
    a = S.start_attempt(db, user, station, "guided", "examiner", "calm")
    v2 = S.revise_station(db, station, {**CONTENT, "scenario": "Changed scenario."}, "test")
    assert db.get(StationVersion, v1).scenario == CONTENT["scenario"], "approved version must not change"
    S.approve_version(db, station, v2, "test")
    S.publish(db, station, "test")
    assert station.current_version_id == v2.id
    assert a.version_id == v1, "an attempt stays on the version it was started on"


def test_admin_endpoints_need_admin(station, users):
    c = client()
    _, hs = users["student"]
    _, ha = users["admin"]
    for method, path in (("get", "/api/admin/stations"), ("post", f"/api/admin/stations/{station.id}/publish"),
                         ("get", "/api/admin/station-challenges")):
        assert getattr(c, method)(path).status_code == 401
        assert getattr(c, method)(path, headers=hs).status_code == 403
    assert c.get("/api/admin/stations", headers=ha).status_code == 200


# ─── The examiner ─────────────────────────────────────────────────────────────

def test_examiner_uses_reviewed_questions_and_timed_mode_hides_feedback(db, station, users, fake):
    user, _ = users["student"]
    a = S.start_attempt(db, user, station, "timed", "strict", "high")
    assert "What is your first concern?" in a.transcript[0]["text"]
    fake.turn = {"action": "follow_up", "follow_up_index": 0, "teaching": "WRONG: the answer is X", "hint": "secret"}
    out = S.candidate_turn(db, a, "It is physiological jaundice", "sure", want_hint=True)
    msg = out["examiner"]["text"]
    assert "Why at this age?" in msg, "follow-ups are the reviewed text, verbatim"
    assert "WRONG" not in msg and "secret" not in msg, "timed mode must not reveal correctness"
    assert a.hints_used == 0
    fake.turn = {"action": "follow_up", "follow_up_index": 0, "teaching": "", "hint": ""}
    out = S.candidate_turn(db, a, "Because under 24 hours", "unsure")
    assert "Total serum bilirubin is 210" in out["examiner"]["text"], "one follow-up per question, then staged info"


def test_examiner_continues_without_the_ai(db, station, users, monkeypatch):
    user, _ = users["student"]

    def broken(*a, **k):
        raise RuntimeError("AI down")
    monkeypatch.setattr(S, "_default_llm", broken)
    a = S.start_attempt(db, user, station, "timed", "examiner", "realistic")
    out = S.candidate_turn(db, a, "Pathological jaundice", "sure")
    assert "What will you do now?" in out["examiner"]["text"]


def test_guided_hint_is_recorded(db, station, users, fake):
    user, _ = users["student"]
    a = S.start_attempt(db, user, station, "guided", "tutor", "calm")
    fake.turn = {"action": "next", "follow_up_index": None, "teaching": "Think about timing.", "hint": "Consider age."}
    out = S.candidate_turn(db, a, "Not sure", "guess", want_hint=True)
    assert "Hint: Consider age." in out["examiner"]["text"] and a.hints_used == 1
    assert a.transcript[1]["hint_after"] is True


def test_timed_deadline_is_enforced(db, station, users, fake):
    user, _ = users["student"]
    a = S.start_attempt(db, user, station, "timed", "examiner", "realistic")
    a.deadline = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    out = S.candidate_turn(db, a, "late answer", "sure")
    assert out == {"ended": True, "reason": "time"} and a.state == "submitted"


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _sat(db, station, user, fake, answers, hint_on_first=False):
    a = S.start_attempt(db, user, station, "guided", "examiner", "calm")
    for i, text in enumerate(answers):
        fake.turn = {"action": "next", "follow_up_index": None, "teaching": "", "hint": "nudge"}
        S.candidate_turn(db, a, text, "sure", want_hint=hint_on_first and i == 0)
    return a


def test_scoring_safeguards(db, station, users, fake):
    user, _ = users["student"]
    a = _sat(db, station, user, fake, ["I am not sure", "jaundice before 24 hours is pathological, so measure the bilirubin",
                                       "give a bolus of insulin"], hint_on_first=True)
    crit = criteria(db, station)
    turns = [i for i, t in enumerate(a.transcript) if t["role"] == "candidate"]
    fake.scores = lambda: {"scores": [
        # earned after the hint on item 0: capped at prompted_credit (0.5 x 2 = 1)
        {"criterion_id": crit[0].id, "marks": 2, "evidence_quote": "jaundice before 24 hours is pathological",
         "turn": turns[1], "rationale": "stated", "uncertainty": "none", "unsafe": False},
        # credit with a quote the candidate never said: flagged, high uncertainty
        {"criterion_id": crit[1].id, "marks": 2, "evidence_quote": "I would check the serum bilirubin urgently",
         "turn": turns[1], "rationale": "?", "uncertainty": "none", "unsafe": False},
        # unsafe finding with a real quote: applied, marks 0
        {"criterion_id": crit[2].id, "marks": 1, "evidence_quote": "give a bolus of insulin", "turn": turns[2],
         "rationale": "dangerous", "uncertainty": "none", "unsafe": True},
        # criterion 3 missing from the AI reply: flagged
    ]}
    S.submit(db, a)
    S.score_attempt(db, a)
    by = {s.criterion_id: s for s in db.query(StationScore).filter(StationScore.attempt_id == a.id)}
    assert by[crit[0].id].marks_awarded == 1.0 and by[crit[0].id].prompted
    assert by[crit[1].id].flagged and by[crit[1].id].uncertainty == "high"
    assert by[crit[2].id].unsafe and by[crit[2].id].marks_awarded == 0 and by[crit[2].id].error_class == "safety"
    assert by[crit[3].id].flagged
    d = S.debrief(db, a)
    assert d["simulated"] and "Not an official CPSP result" in d["result_note"]
    assert "no pass/fail" in d["result_note"], "flagged marks: no pass/fail verdict"
    assert any(f["unsafe"] for f in d["safety_findings"])


def test_unsafe_without_quote_is_not_applied(db, station, users, fake):
    user, _ = users["student"]
    a = _sat(db, station, user, fake, ["phototherapy and recheck", "exchange if encephalopathy"])
    crit = criteria(db, station)
    fake.scores = lambda: {"scores": [{"criterion_id": c.id, "marks": c.marks, "evidence_quote": "", "turn": None,
                                       "rationale": "", "uncertainty": "low", "unsafe": c.safety_critical} for c in crit]}
    S.submit(db, a)
    S.score_attempt(db, a)
    safe = next(s for s in db.query(StationScore).filter(StationScore.attempt_id == a.id)
                if s.criterion_id == crit[2].id)
    assert not safe.unsafe and safe.flagged, "an unquoted unsafe finding is flagged for review, never applied"


def test_scoring_pending_when_ai_down_and_submit_idempotent(db, station, users, fake, monkeypatch):
    user, h = users["student"]
    a = _sat(db, station, user, fake, ["answer one"])

    def broken(*args, **kw):
        raise RuntimeError("AI down")
    monkeypatch.setattr(S, "_default_llm", broken)
    S.score_attempt(db, a)
    assert a.state == "scoring_pending"
    first = a.submitted_at
    S.submit(db, a)
    assert a.submitted_at == first, "a second submit changes nothing"


def test_answer_event_recorded(db, station, users, fake):
    from app.models import AnswerEvent

    user, _ = users["student"]
    a = _sat(db, station, user, fake, ["jaundice before 24 hours is pathological"])
    crit = criteria(db, station)
    fake.scores = lambda: {"scores": [{"criterion_id": c.id, "marks": 0, "evidence_quote": "", "turn": None,
                                       "rationale": "", "uncertainty": "low", "unsafe": False} for c in crit]}
    S.score_attempt(db, a)
    ev = db.query(AnswerEvent).filter(AnswerEvent.session_ref == f"station:{a.id}").one()
    assert ev.source == "toacs" and ev.is_correct is False and ev.user_id == user.id


# ─── Challenges, gold, calibration ────────────────────────────────────────────

def test_challenge_preserves_original_and_promotes_gold(db, station, users, fake):
    user, h = users["student"]
    _, ha = users["admin"]
    a = _sat(db, station, user, fake, ["measure the total serum bilirubin now"])
    crit = criteria(db, station)
    fake.scores = lambda: {"scores": [{"criterion_id": c.id, "marks": 0, "evidence_quote": "", "turn": None,
                                       "rationale": "not said", "uncertainty": "low", "unsafe": False} for c in crit]}
    S.score_attempt(db, a)
    sc = next(s for s in db.query(StationScore).filter(StationScore.attempt_id == a.id) if s.criterion_id == crit[1].id)
    c = client()
    r = c.post(f"/api/station-attempts/{a.id}/challenges", json={"score_id": sc.id, "rationale": "I did say measure the bilirubin"}, headers=h)
    assert r.status_code == 200
    ch_id = r.json()["id"]
    assert c.post(f"/api/station-attempts/{a.id}/challenges", json={"score_id": sc.id, "rationale": "again, again"}, headers=h).status_code == 409
    r = c.post(f"/api/admin/station-challenges/{ch_id}", json={"decision": "upheld", "corrected_marks": 2,
                                                              "note": "Said clearly", "promote_to_gold": True}, headers=ha)
    assert r.status_code == 200 and r.json()["gold_id"]
    db.refresh(sc)
    assert sc.marks_awarded == 0, "the original score row is never edited"
    gold = db.get(StationGold, r.json()["gold_id"])
    assert gold.expert_marks[str(crit[1].id)] == 2 and gold.origin == "challenge"


def test_calibration_gate_and_leave_one_out(db, station, users, fake):
    v = db.get(StationVersion, station.current_version_id)
    crit = criteria(db, station)
    golds = []
    for label in ("strong", "borderline", "unsafe"):
        g = StationGold(version_id=v.id, label=label, transcript=[{"role": "candidate", "text": f"{label} answer text here"}],
                        expert_marks={str(c.id): c.marks for c in crit}, marked_by="expert", marked_at=datetime.utcnow())
        db.add(g)
        golds.append(g)
    db.commit()
    seen_examples = []
    orig = S._examples_text

    def spy(db_, version_id, exclude, criteria_, k=2):
        out = orig(db_, version_id, exclude, criteria_, k)
        seen_examples.append((set(exclude), out[1]))
        return out
    S._examples_text = spy
    try:
        fake.scores = lambda: {"scores": [{"criterion_id": c.id, "marks": c.marks, "evidence_quote": "answer text here",
                                           "turn": 0, "rationale": "", "uncertainty": "none", "unsafe": False} for c in crit]}
        run = S.run_calibration(db, v, "expert")
        assert run.passed and run.metrics["mae"] == 0 and station.readiness == "assessment_ready"
        for exclude, used in seen_examples:
            assert not (exclude & set(used)), "a gold answer is never its own example"
        fake.scores = lambda: {"scores": [{"criterion_id": c.id, "marks": 0, "evidence_quote": "", "turn": None,
                                           "rationale": "", "uncertainty": "none", "unsafe": False} for c in crit]}
        run = S.run_calibration(db, v, "expert")
        assert not run.passed and station.readiness == "practice_only"
    finally:
        S._examples_text = orig


def test_account_deletion_removes_station_attempts(db, station, fake):
    from app.account import delete_user_data
    from app.models import User

    u = User(username=f"st_del_{uuid.uuid4().hex[:6]}", password_hash="x", role="student")
    db.add(u)
    db.commit()
    a = _sat(db, station, u, fake, ["some answer text"])
    aid = a.id
    delete_user_data(db, u.id)
    db.expire_all()
    assert db.get(StationAttempt, aid) is None


# ─── Suggested level and circuits ─────────────────────────────────────────────

def test_learner_level_follows_results(db, station, users, fake):
    user, _ = users["student"]
    assert S.learner_level(db, user.id, "Paediatrics")["level"] == 2
    crit = criteria(db, station)
    fake.scores = lambda: {"scores": [{"criterion_id": c.id, "marks": c.marks, "evidence_quote": "answer text for marking",
                                       "turn": None, "rationale": "", "uncertainty": "none", "unsafe": False} for c in crit]}
    for _ in range(2):
        a = _sat(db, station, user, fake, ["answer text for marking"])
        S.score_attempt(db, a)
    lv = S.learner_level(db, user.id, "Paediatrics")
    assert lv["level"] == 4 and "100%" in lv["reason"], lv     # difficulty 3, all marks -> suggest 4


def test_circuit_runs_stations_in_order(db, station, users, fake):
    user, h = users["student"]
    second = Station(specialty="Paediatrics", tos_exam_id="pget", tos_paper_id="p2-paediatric-medicine",
                     tos_topic_id="other-topic", title="[TEST] second station", status="draft", provenance="authored")
    db.add(second)
    db.flush()
    v = S._new_version(db, second, CONTENT, "test")
    second.current_version_id = v.id
    db.commit()
    S.approve_version(db, second, v, "test")
    S.publish(db, second, "test")
    c = client()
    try:
        r = c.post("/api/station-circuits", json={"specialty": "Paediatrics", "n": 2}, headers=h).json()
        cid, first = r["circuit_id"], r["attempt_id"]
        assert r["stations"] == 2 and db.get(StationAttempt, first).mode == "timed"
        assert c.post(f"/api/station-circuits/{cid}/next", headers=h).json()["attempt_id"] == first, \
            "the next station only starts after this one is submitted"
        crit_ids = {sid: [x.id for x in S.criteria_of(db, db.get(Station, sid).current_version_id)] for sid in (station.id, second.id)}
        fake.scores = lambda: {"scores": [{"criterion_id": i, "marks": 1, "evidence_quote": "", "turn": None, "rationale": "",
                                           "uncertainty": "low", "unsafe": False} for ids in crit_ids.values() for i in ids]}
        c.post(f"/api/station-attempts/{first}/submit", headers=h)
        nxt = c.post(f"/api/station-circuits/{cid}/next", headers=h).json()
        assert nxt["attempt_id"] and nxt["attempt_id"] != first
        c.post(f"/api/station-attempts/{nxt['attempt_id']}/submit", headers=h)
        done = c.post(f"/api/station-circuits/{cid}/next", headers=h).json()
        assert done["attempt_id"] is None and done["done"] and done["marks_available"] > 0
        assert "Provisional" in done["note"]
    finally:
        from app.models import StudySession
        db.rollback()
        db.query(StudySession).filter(StudySession.user_id == user.id, StudySession.kind == "toacs_circuit").delete()
        db.query(StationAttempt).filter(StationAttempt.station_id == second.id).delete()
        ids = [str(second.id), str(v.id)]
        db.query(StationAudit).filter(StationAudit.entity_id.in_(ids)).delete(synchronize_session=False)
        db.query(Station).filter(Station.id == second.id).update({"current_version_id": None})
        db.commit()
        db.delete(db.get(Station, second.id))
        db.commit()
