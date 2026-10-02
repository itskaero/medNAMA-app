"""Practice: the shared Subject -> Topic list with question counts (app/practice_scope.py, app/taxonomy.py)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_student_or_admin
from app.deps import get_db
from app.models import User

router = APIRouter()


@router.get("/api/practice/tree")
def practice_tree_route(db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    """Subjects and their topics with how many bank / past-paper questions each has, and the past-paper years."""
    from app.practice_scope import practice_tree

    return practice_tree(db, current_user)
