"""End-to-end check of the custom exam builder and programme availability.

Calls the router functions directly with a real user and session, so it exercises the same
filtering, counting and assembly the HTTP routes do without needing a login cookie.

Usage (from backend/):
    python scripts/test_mock_builder.py
    python scripts/test_mock_builder.py --user admin
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import MCQ, User, WeeklyMock  # noqa: E402
from app.routers.mocks_custom import (CustomMockRequest, custom_create, custom_preview,  # noqa: E402
                                      programme_availability)

logging.basicConfig(level=logging.WARNING)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="student")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == args.user).first()
        if user is None:
            print(f"no such user: {args.user}")
            return
        print(f"user {user.username} (role={user.role})\n")

        print("=== programme availability ===")
        for p in programme_availability(db=db, current_user=user)["programmes"]:
            flag = "startable" if p["can_start"] else "UNAVAILABLE"
            print(f"  {p['programme']:<8} {p['available']:>6} questions  {flag:<12} {p['note']}")

        print("\n=== preview: whole FCPS-I programme ===")
        pv = custom_preview(CustomMockRequest(programme='FCPS-I', questions=100),
                            db=db, current_user=user)
        print(f"  total={pv['total']} unseen={pv['unseen']} would_build={pv['questions']} "
              f"can_start={pv['can_start']}")
        print(f"  subjects: {[(s['subject'], s['count']) for s in pv['by_subject'][:6]]}")
        for n in pv["notes"]:
            print(f"  note: {n}")

        print("\n=== preview: narrow scope (Physiology, bank only) ===")
        narrow = {"sources": ["bank"], "subjects": ["Physiology"], "topics": [], "years": []}
        pv2 = custom_preview(CustomMockRequest(scope=narrow, questions=50), db=db, current_user=user)
        print(f"  total={pv2['total']} unseen={pv2['unseen']} would_build={pv2['questions']} "
              f"can_start={pv2['can_start']}")
        for n in pv2["notes"]:
            print(f"  note: {n}")

        # A year filter only applies when past papers are in scope (the bank has no years), so
        # an impossible selection has to be impossible for the past-paper source itself.
        print("\n=== preview: impossible scope (past papers, year 1901) ===")
        silly = {"sources": ["past"], "subjects": ["Physiology"], "topics": [], "years": [1901]}
        pv3 = custom_preview(CustomMockRequest(scope=silly, questions=100), db=db, current_user=user)
        print(f"  total={pv3['total']} can_start={pv3['can_start']}")
        for n in pv3["notes"]:
            print(f"  note: {n}")
        try:
            custom_create(CustomMockRequest(scope=silly, questions=100), db=db, current_user=user)
            print("  !! create succeeded on an empty scope (should have been refused)")
        except Exception as e:
            print(f"  create refused as expected: {getattr(e, 'detail', e)}")

        print("\n=== create: 20-question paper from the narrow scope ===")
        made = custom_create(CustomMockRequest(scope=narrow, questions=20, minutes=25,
                                               label="Physiology drill"),
                             db=db, current_user=user)
        print(f"  mock_id={made['mock_id']} title={made['title']!r}")
        print(f"  questions={made['questions']} minutes={made['minutes']}")
        print(f"  subject spread: {made['subjects'][:8]}")

        mock = db.get(WeeklyMock, made["mock_id"])
        ids = mock.mcq_ids or []
        print(f"  stored: part={mock.part!r} track={mock.track!r} ids={len(ids)} "
              f"distinct={len(set(ids))}")
        bad = db.query(MCQ).filter(MCQ.id.in_(ids), MCQ.status != "ready").count()
        print(f"  not-ready questions in the paper: {bad}")

        from app.study_modes import owned_mock
        print(f"  owned_mock() recognises it: {owned_mock(db, user, mock.id) is not None}")
        other = db.query(User).filter(User.id != user.id).first()
        if other is not None:
            print(f"  another user can open it: {owned_mock(db, other, mock.id) is not None} "
                  f"(must be False)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
