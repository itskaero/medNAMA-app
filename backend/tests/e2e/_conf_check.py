"""Verify confidence is collected before reveal and saved with the answer (Q28/§8)."""
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from app.auth import create_access_token  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

BASE = "http://127.0.0.1:8000"
import urllib.request  # noqa: E402

token = create_access_token({"sub": "student"}, timedelta(hours=6))
H = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None,
                                 headers=H, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


start = call("POST", "/api/quizzes/start", {
    "scope": {"sources": ["bank"], "subjects": [], "topics": [], "years": []},
    "num_questions": 3, "feedback_mode": "tutor", "label": "conf check"})
mcq = start["mcqs"][0]
mcq_id = mcq["id"]
print(f"started quiz {start['quiz_attempt_id']}, mcq {mcq_id}")

ans = call("POST", f"/api/quizzes/{start['quiz_attempt_id']}/answer",
           {"mcq_id": mcq_id, "selected_option": "A", "confidence": "guess"})
print(f"answered: saved={ans['saved']} is_correct={ans['is_correct']} (key revealed: {'correct_option' in ans and ans['correct_option'] is not None})")

db = SessionLocal()
uid = db.execute(text("SELECT id FROM users WHERE username='student'")).scalar()
row = db.execute(text("SELECT confidence, is_correct FROM answer_events WHERE user_id=:u AND mcq_id=:m ORDER BY id DESC LIMIT 1"),
                 {"u": uid, "m": mcq_id}).first()
db.close()
print(f"answer_events.confidence = {row.confidence!r} (expected 'guess')")
print("CONFIDENCE SAVED:", row.confidence == "guess")
