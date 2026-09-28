"""Stats (every answer, by feature and session) and the older detailed stats.

Moved verbatim from app/main.py (routes keep their paths)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.models import User, MCQ, QuizAttempt, AttemptAnswer
from app.auth import require_student_or_admin
from app.deps import get_db

router = APIRouter()

# ======================== DETAILED TELEMETRY STATS ========================

@router.get("/api/stats/overview")
def stats_overview(sessions_offset: int = 0, db: Session = Depends(get_db),
                   current_user: User = Depends(require_student_or_admin)):
    """Every answer from every feature: totals, by feature, by week, by subject, and each session."""
    from app.stats import overview

    return overview(db, current_user, sessions_offset=sessions_offset)

@router.get("/api/dashboard/detailed-stats")
def get_detailed_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Fetches comprehensive mock attempts stats, category accuracies, and attempt trends for Chart.js dashboard charts."""
    attempts = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.completed_at != None
    ).order_by(QuizAttempt.started_at.asc()).all()
    
    total_attempts = len(attempts)
    if total_attempts == 0:
        return {
            "total_attempts": 0,
            "avg_accuracy": 0,
            "total_questions": 0,
            "history_trend": [],
            "category_breakdown": {}
        }
        
    total_score = sum(a.score for a in attempts if a.score)
    total_questions = sum(a.total_questions for a in attempts if a.total_questions)
    avg_accuracy = round((total_score / total_questions) * 100, 1) if total_questions > 0 else 0
    
    # Accuracy trend over time (last 15 completed mock sessions)
    trend = []
    for a in attempts[-15:]:
        acc = round((a.score / a.total_questions) * 100, 1) if a.total_questions and a.total_questions > 0 else 0
        date_str = a.started_at.strftime("%b %d") if a.started_at else "N/A"
        trend.append({
            "attempt_id": a.id,
            "date": date_str,
            "accuracy": acc,
            "score": a.score,
            "total": a.total_questions
        })
        
    # Group and count correct answers and totals per subject category
    category_breakdown = {}
    answers_query = db.query(AttemptAnswer.is_correct, MCQ.main_category).join(
        MCQ, MCQ.id == AttemptAnswer.mcq_id
    ).join(
        QuizAttempt, QuizAttempt.id == AttemptAnswer.quiz_attempt_id
    ).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.completed_at != None
    ).all()
    
    for is_correct, cat in answers_query:
        category_name = cat if cat else "General"
        if category_name not in category_breakdown:
            category_breakdown[category_name] = {"correct": 0, "total": 0}
        category_breakdown[category_name]["total"] += 1
        if is_correct:
            category_breakdown[category_name]["correct"] += 1
            
    formatted_breakdown = {}
    for cat, stats in category_breakdown.items():
        formatted_breakdown[cat] = {
            "total_questions": stats["total"],
            "correct_answers": stats["correct"],
            "accuracy": round((stats["correct"] / stats["total"]) * 100, 1)
        }
        
    return {
        "total_attempts": total_attempts,
        "avg_accuracy": avg_accuracy,
        "total_questions": total_questions,
        "history_trend": trend,
        "category_breakdown": formatted_breakdown
    }
