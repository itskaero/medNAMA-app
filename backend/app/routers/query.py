"""Textbook questions, figures, MCQ explanations and practice quizzes.

Moved verbatim from app/main.py (routes keep their paths)."""

import logging
import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.database import engine
from app.generation import generate_answer, generate_mcq_explanation
from app.models import Book, Figure, User, MCQ, QuizAttempt, AttemptAnswer
from app.auth import rate_limiter, require_admin, require_student_or_admin
from app.deps import QueryRequest, get_db, logger

router = APIRouter()

# ======================== QUERYING & DIAGRAMS ========================

@router.post(
    "/api/query",
    dependencies=[Depends(rate_limiter(limit=15, window=60))],
)
def query_rag(
    request: QueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Processes RAG question answering using hybrid search and DeepSeek."""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    answer_json = generate_answer(
        session=db, query=request.query, confidence_threshold=request.confidence_threshold
    )
    return answer_json

@router.get("/api/figures/{figure_id}")
def get_figure_image(
    figure_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Serves raw diagram image bytes directly from database with correct content headers."""
    figure = db.query(Figure).filter(Figure.id == figure_id).first()
    if not figure:
        raise HTTPException(status_code=404, detail="Figure not found.")

    return Response(content=figure.image_data, media_type=figure.mime_type)

@router.get("/api/dashboard/stats")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Returns general statistics and topic lists for the student/admin dashboard."""
    from sqlalchemy import func
    
    from app.retention import access_scope

    total_books = db.query(Book).count()
    # What this user can practise: restricted past papers only with access, recall-derived rows never.
    total_mcqs = access_scope(db.query(MCQ).filter(MCQ.status != "private"), current_user).count()
    total_quizzes_taken = db.query(QuizAttempt).filter(QuizAttempt.user_id == current_user.id).count()
    
    attempts = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.score.isnot(None)
    ).all()
    
    # Every answered question counts (Daily Dose, past papers, mocks...), not only finished quizzes: the same
    # number Stats shows.
    from app.stats import accuracy as _accuracy

    avg_score = _accuracy(db, current_user) or 0.0
        
    # Get distinct main_category, sub_category and their counts
    categories_query = db.query(
        MCQ.main_category,
        MCQ.sub_category,
        func.count(MCQ.id)
    ).filter(MCQ.status != "private")
    from app.retention import access_scope as _access_scope

    categories_query = _access_scope(categories_query, current_user).group_by(MCQ.main_category, MCQ.sub_category).all()
    
    categories_map = {}
    for main, sub, count in categories_query:
        if not main or not sub:
            continue
        if main not in categories_map:
            categories_map[main] = []
        categories_map[main].append({"name": sub, "count": count})
        
    for main in categories_map:
        categories_map[main] = sorted(categories_map[main], key=lambda x: x["name"])
        
    categories_list = [
        {"main_category": main, "sub_categories": sub_list}
        for main, sub_list in sorted(categories_map.items())
    ]

    recent_attempts = []
    db_attempts = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.score.isnot(None)
    ).order_by(QuizAttempt.completed_at.desc()).limit(5).all()

    for att in db_attempts:
        first_ans = db.query(AttemptAnswer).filter(AttemptAnswer.quiz_attempt_id == att.id).first()
        category_name = "General Practice"
        if first_ans:
            mcq = db.query(MCQ).filter(MCQ.id == first_ans.mcq_id).first()
            if mcq:
                category_name = mcq.sub_category or mcq.main_category or "General Practice"
        
        recent_attempts.append({
            "id": att.id,
            "started_at": att.started_at.isoformat() if att.started_at else None,
            "completed_at": att.completed_at.isoformat() if att.completed_at else None,
            "score": att.score,
            "total_questions": att.total_questions,
            "category": category_name
        })

    return {
        "total_books": total_books,
        "total_mcqs": total_mcqs,
        "total_quizzes_taken": total_quizzes_taken,
        "average_score": round(avg_score, 1),
        "categories": categories_list,
        "recent_attempts": recent_attempts
    }

@router.post("/api/mcqs/{mcq_id}/explain")
def explain_mcq_endpoint(
    mcq_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """On-demand RAG-grounded explanation generation and caching for a specific MCQ."""
    from app.retention import can_see_mcq

    mcq = db.query(MCQ).filter(MCQ.id == mcq_id).first()
    if not mcq or not can_see_mcq(current_user, mcq):
        raise HTTPException(status_code=404, detail="MCQ not found.")

    # Return cached explanation if present
    if mcq.explanation_markdown and mcq.explanation_markdown.strip() and mcq.status in ("ready", "private"):
        return {
            "answer_markdown": mcq.explanation_markdown,
            "citations": mcq.explanation_citations or [],
            "figures": mcq.explanation_figures or []
        }

    # Generate and cache explanation
    explanation_data = generate_mcq_explanation(db, mcq)
    
    # Save cache back to DB
    mcq.explanation_markdown = explanation_data.get("answer_markdown")
    mcq.explanation_citations = explanation_data.get("citations")
    mcq.explanation_figures = explanation_data.get("figures")
    if mcq.status != "private":   # explaining a private question must not publish it
        mcq.status = "ready"
    db.commit()

    return explanation_data

# ─── AI Quiz Generation System ───────────────────────────────────

class GenerateAiQuizRequest(BaseModel):
    prompt: str
    book_id: int | None = None
    page_number: int | None = None
    count: int | None = None
    difficulty: int | None = None  # 1 (easy) - 5 (hard); falls back to AI_MCQ_DIFFICULTY env
    exam_profile: str | None = None  # 'fcps' (A-E, default) | 'usmle' (A-E vignette) | 'quick' (A-D recall)
    request_id: str | None = None  # client idempotency key; a retry returns the same set
    chapter: str | None = None     # restrict to one chapter of book_id (Study-a-chapter)

def _resolve_quiz_params(req: GenerateAiQuizRequest) -> dict:
    """Validate a quiz request and resolve page / count / difficulty from the prompt and env."""
    import re

    prompt_text = req.prompt.strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    page_num = req.page_number
    if page_num is None:
        page_match = re.search(r"page\s*#?\s*(\d+)", prompt_text, re.IGNORECASE)
        if page_match:
            page_num = int(page_match.group(1))

    # Multiples of 5 only, max 20
    ALLOWED_COUNTS = (5, 10, 15, 20)
    mcq_count = req.count or 5
    count_match = re.search(r"(\d+)\s*(?:questions?|mcqs?|items?)", prompt_text, re.IGNORECASE)
    if count_match:
        mcq_count = int(count_match.group(1))
    if mcq_count not in ALLOWED_COUNTS:
        raise HTTPException(
            status_code=400,
            detail="count must be a multiple of 5 between 5 and 20 (5, 10, 15, or 20).",
        )

    # Difficulty: explicit request > AI_MCQ_DIFFICULTY env override > unspecified
    difficulty = req.difficulty
    if difficulty is None:
        env_diff = os.environ.get("AI_MCQ_DIFFICULTY", "").strip()
        if env_diff.isdigit():
            difficulty = int(env_diff)
    if difficulty is not None and not (1 <= difficulty <= 5):
        raise HTTPException(status_code=400, detail="difficulty must be an integer between 1 and 5.")

    from app.llm import llm_configured

    if not llm_configured("fast"):
        raise HTTPException(status_code=400, detail="DEEPSEEK_API_KEY is not configured on the server.")

    return {
        "prompt_text": prompt_text,
        "book_id": req.book_id,
        "page_num": page_num,
        "mcq_count": mcq_count,
        "difficulty": difficulty,
        "exam_profile": req.exam_profile,
        "request_id": req.request_id,
        "chapter": (req.chapter or None) if req.book_id else None,
    }

@router.post("/api/chat/generate-ai-quiz")
def generate_ai_quiz(
    req: GenerateAiQuizRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Generates MCQs synchronously (kept for API clients; the UI uses the job endpoints)."""
    from app.quiz_generation import QuizGenerationError, generate_quiz_set

    params = _resolve_quiz_params(req)
    try:
        return generate_quiz_set(db, **params)
    except QuizGenerationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

@router.post("/api/chat/generate-ai-quiz/jobs", status_code=status.HTTP_202_ACCEPTED)
def start_ai_quiz_job(
    req: GenerateAiQuizRequest,
    current_user: User = Depends(require_student_or_admin),
):
    """Start MCQ generation in the background and return a job id to poll.

    The request returns immediately, so no proxy or browser timeout can cut it
    off however long generation takes. Idempotent on request_id.
    """
    from app.quiz_generation import start_quiz_job

    params = _resolve_quiz_params(req)
    return start_quiz_job(params)

@router.get("/api/chat/generate-ai-quiz/jobs/{job_id}")
def get_ai_quiz_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Poll a background MCQ job: {status: running|done|failed, stage?, result?, detail?}."""
    from app.quiz_generation import get_quiz_job

    job = get_quiz_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Quiz job not found.")
    return job

@router.get("/api/chat/ai-quizzes/{quiz_set_id}")
def get_ai_quiz_set(
    quiz_set_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Return a saved AI quiz set (used to recover a set whose generate request timed out client-side)."""
    from app.quiz_generation import load_quiz_set, serialize_quiz_set

    mcqs = load_quiz_set(db, quiz_set_id)
    if not mcqs:
        raise HTTPException(status_code=404, detail="Quiz set not found.")
    return serialize_quiz_set(mcqs, quiz_set_id)

@router.get("/api/chat/ai-quizzes")
def get_ai_quizzes_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Returns list of distinct AI-generated quiz sets for user history (only what this user can open)."""
    from app.retention import access_scope

    results = (
        access_scope(db.query(
            MCQ.quiz_set_id,
            MCQ.quiz_set_title,
            func.count(MCQ.id).label("question_count"),
            func.max(MCQ.topic).label("topic"),
            func.max(MCQ.difficulty).label("difficulty"),
        ), current_user)
        .filter(MCQ.quiz_set_id != None)
        .group_by(MCQ.quiz_set_id, MCQ.quiz_set_title)
        .order_by(func.max(MCQ.id).desc())
        .all()
    )

    return [
        {
            "quiz_set_id": r.quiz_set_id,
            "quiz_set_title": r.quiz_set_title or "AI Quiz Set",
            "question_count": r.question_count,
            "topic": r.topic,
            "difficulty": r.difficulty,
        }
        for r in results
    ]

@router.delete("/api/chat/ai-quizzes/{quiz_set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_quiz_set(
    quiz_set_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Deletes all MCQs in a specific AI quiz set. Admin only: sets are shared (no owner is recorded), so a
    student deleting one would remove it, and its questions' answer links, for everyone."""
    db.query(MCQ).filter(MCQ.quiz_set_id == quiz_set_id).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

class HardenMcqsRequest(BaseModel):
    """Ask AI to rewrite existing questions into harder versions (same facts, harder
    statements & options) — app/hardening.py. Either list seed_ids explicitly, or give
    the same topic filters Mock Builder uses; the questions are picked from those."""
    seed_ids: list[int] | None = None
    categories: list[str] | None = None
    sub_categories: list[str] | None = None
    topics: list[str] | None = None
    num_questions: int = 5        # 1..50 (one harder rewrite per seed)
    difficulty: int = 4           # 4 = multi-step reasoning, 5 = deep integration
    request_id: str | None = None # idempotency key; a retry returns the same set
    label: str | None = None      # shows in the set's title ("Hardened · <label>")
    scope: dict | None = None     # or a Practice selection (app/practice_scope.py)

@router.post("/api/chat/harden/preview")
def harden_preview_route(
    req: HardenMcqsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """How a harden request would split across the picked subjects/topics, and roughly how long it takes."""
    from app.hardening import HardenError, preview

    try:
        return preview(db, current_user, req.model_dump(exclude_none=True))
    except HardenError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/api/chat/harden/jobs", status_code=status.HTTP_202_ACCEPTED)
def start_harden_job_route(
    req: HardenMcqsRequest,
    current_user: User = Depends(require_student_or_admin),
):
    """Start harder-rewrite generation in the background; poll /jobs/{job_id}."""
    from app.hardening import start_harden_job

    out = start_harden_job(current_user, req.model_dump(exclude_none=True))
    if out.get("error"):
        raise HTTPException(status_code=400, detail=out["error"])
    return out

@router.get("/api/chat/harden/jobs/{job_id}")
def get_harden_job_route(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Poll a harden job: {status: running|done|failed, progress, result?, detail?}."""
    from app.hardening import get_harden_job

    job = get_harden_job(db, job_id, current_user)   # only its owner; progress while running
    if job is None:
        raise HTTPException(status_code=404, detail="Harden job not found.")
    return job

# ─── Practice Quiz Management (Phase 11) ───────────────────────

class StartQuizRequest(BaseModel):
    quiz_set_id: str | None = None
    categories: list[str] | None = None
    sub_categories: list[str] | None = None
    topics: list[str] | None = None              # mcqs.topic labels, ANDed with the other filters
    num_questions: int = 10
    exclude_mastered: bool = False
    drill_wrong: bool = False                 # answer-only the user's missed MCQs
    prefer_unseen: bool = True                # questions the user has never attempted come first
    timer_mode: str = "none"                      # "none" | "session" | "per_question"
    timer_value: int | None = None                # minutes or seconds
    feedback_mode: str = "tutor"                  # "tutor" | "board"
    past_paper_exam: str | None = None            # e.g. "FCPS Part 1": questions from its past papers
    years: list[int] | None = None                # past-paper years
    tags: dict[str, list[str]] | None = None      # {"subject": [...], "topic": [...], "specialty": [...]}
    twists: bool = False                          # with past-paper filters: their twists instead (app/twists.py)
    include_ids: list[int] | None = None         # with quiz_set_id: also these questions (a harder set's fill)
    scope: dict | None = None                     # a Practice selection (app/practice_scope.py): sources, subjects,
                                                  # subject|topic pairs, years; with twists=True, their twists
    label: str | None = None                      # the session's name in Stats ("Past papers · FCPS Part 1 · 2024")

class SelectedAnswer(BaseModel):
    mcq_id: int
    selected_option: str
    confidence: str | None = None   # 'sure' | 'unsure' | 'guess' (retention engine)

class SubmitQuizRequest(BaseModel):
    answers: list[SelectedAnswer]

@router.post("/api/quizzes/start")
def start_quiz_endpoint(
    req: StartQuizRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Generates a randomized practice quiz, creates a QuizAttempt record, and returns the questions."""
    from sqlalchemy import func
    from app.models import AttemptAnswer, QuizAttempt

    from app.retention import access_scope

    # Private (recall-derived) questions are served only through the Daily Dose; restricted
    # (imported past-paper) questions only to users allowed by PAST_PAPERS_ACCESS.
    query = access_scope(db.query(MCQ).filter(MCQ.status != "private"), current_user)
    if req.scope is not None:
        from app.practice_scope import restrict

        if req.twists:   # twists written from the selection's past-paper questions
            seeds = restrict(db, db.query(MCQ.id), {**req.scope, "sources": ["past"]})
            query = query.filter(MCQ.twist_of.in_(seeds))
        else:
            query = restrict(db, query, req.scope)
    elif req.past_paper_exam or req.years or req.tags:
        from app.past_papers import past_paper_filter

        if req.twists:   # the twists written from the selected past-paper questions
            seeds = past_paper_filter(db, db.query(MCQ.id), req.past_paper_exam, req.years, req.tags)
            query = query.filter(MCQ.twist_of.in_(seeds))
        else:
            query = past_paper_filter(db, query, req.past_paper_exam, req.years, req.tags)
    
    # Drill mode: only previously-missed questions (user-scoped by construct)
    if req.drill_wrong:
        wrong_ids = (
            db.query(AttemptAnswer.mcq_id)
            .join(QuizAttempt, QuizAttempt.id == AttemptAnswer.quiz_attempt_id)
            .filter(
                QuizAttempt.user_id == current_user.id,
                AttemptAnswer.is_correct.is_(False),
            )
            .distinct()
            .subquery()
        )
        query = query.filter(MCQ.id.in_(wrong_ids))

    # Topic filters
    if req.quiz_set_id:
        # A harder set plus the originals that filled its empty slots ("as written").
        query = query.filter(or_(MCQ.quiz_set_id == req.quiz_set_id, MCQ.id.in_(req.include_ids))
                             if req.include_ids else MCQ.quiz_set_id == req.quiz_set_id)
    elif req.sub_categories:
        query = query.filter(MCQ.sub_category.in_(req.sub_categories))
    elif req.categories:
        query = query.filter(MCQ.main_category.in_(req.categories))
    if req.topics:
        query = query.filter(MCQ.topic.in_(req.topics))
        
    # Exclude mastered questions (answered correctly in any past attempt)
    if req.exclude_mastered:
        mastered_subquery = db.query(AttemptAnswer.mcq_id).join(
            QuizAttempt, QuizAttempt.id == AttemptAnswer.quiz_attempt_id
        ).filter(
            QuizAttempt.user_id == current_user.id,
            AttemptAnswer.is_correct == True
        ).subquery()
        query = query.filter(MCQ.id.notin_(mastered_subquery))
        
    # "Seen" = answered anywhere: quiz attempts, and the Daily Dose / sprint / mocks (answer_events).
    # Answering one archive's version of a past-paper question counts for its other versions too.
    from app.models import AnswerEvent
    from app.past_papers import distinct_by_group, group_key, with_recall_groups

    seen_ids = with_recall_groups(db, db.query(AttemptAnswer.mcq_id).join(
        QuizAttempt, QuizAttempt.id == AttemptAnswer.quiz_attempt_id
    ).filter(QuizAttempt.user_id == current_user.id).union(
        db.query(AnswerEvent.mcq_id).filter(AnswerEvent.user_id == current_user.id, AnswerEvent.mcq_id.isnot(None))
    ))
    # Size of the whole selection and how much of it is still unanswered, so "work through all"
    # sessions can show "N left" and offer the next batch. Two versions of one question count once.
    count_q = query.order_by(None).with_entities(func.count(func.distinct(group_key())))
    total_in_scope = count_q.scalar() or 0
    unseen_in_scope = count_q.filter(MCQ.id.notin_(seen_ids)).scalar() or 0

    # Unseen first: never-attempted questions before ones already answered, so a
    # growing bank keeps producing fresh practice. Random within each group.
    # Over-fetch, then keep one version of each recalled question.
    fetch = req.num_questions * 2 + 10
    if req.prefer_unseen and not req.drill_wrong:
        from sqlalchemy import case

        seen_rank = case((MCQ.id.in_(seen_ids), 1), else_=0)
        mcqs = query.order_by(seen_rank, func.random()).limit(fetch).all()
    else:
        mcqs = query.order_by(func.random()).limit(fetch).all()
    mcqs = distinct_by_group(mcqs, req.num_questions)
    
    if not mcqs:
        raise HTTPException(
            status_code=400,
            detail=("No twists have been written for these questions yet: answer a past-paper question and tap "
                    "'Twist it' (the most-asked questions get theirs written in the background)."
                    if req.twists else "No questions found matching the specified filters.")
        )

    default_label = ("Twists" if req.twists else f"Past papers · {req.past_paper_exam}" if req.past_paper_exam
                     else ", ".join(req.sub_categories or req.categories or []) or "Practice")
    attempt = QuizAttempt(
        user_id=current_user.id,
        label=(req.label or default_label)[:200],
        total_questions=len(mcqs),
        timer_mode=req.timer_mode,
        timer_value=req.timer_value,
        feedback_mode=req.feedback_mode
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    
    # Format questions (excluding deep explanation details initially)
    from app.past_papers import paper_years, question_media, recall_info

    media = question_media(db, [m.id for m in mcqs])
    years = paper_years(db, [m.id for m in mcqs])
    recalls = recall_info(db, mcqs)
    mcqs_data = []
    for m in mcqs:
        mcqs_data.append({
            "id": m.id,
            "question_text": m.question_text,
            "options": m.options,
            "correct_option": m.correct_option,
            "main_category": m.main_category,
            "sub_category": m.sub_category,
            "media": media.get(m.id, []),
            "paper_years": years.get(m.id, []),
            **recalls.get(m.id, {}),
        })
        
    return {
        "quiz_attempt_id": attempt.id,
        "total_in_scope": total_in_scope,
        "unseen_in_scope": unseen_in_scope,
        "mcqs": mcqs_data,
        "timer_mode": attempt.timer_mode,
        "timer_value": attempt.timer_value,
        "feedback_mode": attempt.feedback_mode
    }

@router.post("/api/quizzes/{attempt_id}/submit")
def submit_quiz_endpoint(
    attempt_id: int,
    req: SubmitQuizRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Scores a completed practice quiz, records individual choices, and saves results to the database."""
    from datetime import datetime
    
    attempt = db.query(QuizAttempt).filter(
        QuizAttempt.id == attempt_id,
        QuizAttempt.user_id == current_user.id
    ).first()
    
    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Quiz attempt session not found."
        )
        
    if attempt.completed_at is not None:
        raise HTTPException(
            status_code=400,
            detail="This quiz attempt has already been submitted and completed."
        )
        
    # Build a lookup dictionary of MCQs involved in the attempt to minimize DB queries
    mcq_ids = [ans.mcq_id for ans in req.answers]
    mcqs = db.query(MCQ).filter(MCQ.id.in_(mcq_ids)).all()
    mcq_map = {m.id: m for m in mcqs}
    
    correct_count = 0
    attempt_answers = []
    
    for ans in req.answers:
        mcq = mcq_map.get(ans.mcq_id)
        if not mcq:
            raise HTTPException(
                status_code=400,
                detail=f"Question with ID {ans.mcq_id} is invalid or not found."
            )
            
        selected_upper = ans.selected_option.upper().strip()
        correct_upper = mcq.correct_option.upper().strip()
        is_correct = (selected_upper == correct_upper)
        
        if is_correct:
            correct_count += 1
            
        ans_record = AttemptAnswer(
            quiz_attempt_id=attempt.id,
            mcq_id=mcq.id,
            selected_option=ans.selected_option,
            is_correct=is_correct
        )
        db.add(ans_record)
        attempt_answers.append(ans_record)
        
    attempt.score = correct_count
    attempt.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(attempt)

    # Retention engine: log each answer with its confidence; wrong or guessed
    # answers get a concept card (built in the background) and a re-test schedule.
    from app.retention import record_answer

    for ans in req.answers:
        mcq = mcq_map.get(ans.mcq_id)
        if mcq is not None:
            try:
                record_answer(db, current_user.id, mcq, ans.selected_option, ans.confidence or "sure", source="quiz",
                              session_ref=f"quiz:{attempt.id}")
            except Exception:
                logger.exception("Retention logging failed for MCQ %s", ans.mcq_id)
                db.rollback()
    
    return {
        "score": attempt.score,
        "total_questions": attempt.total_questions,
        "completed_at": attempt.completed_at
    }

@router.get("/api/quizzes/{attempt_id}")
def get_quiz_attempt(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Retrieves the details of a previous quiz attempt, including all questions and selected answers."""
    attempt = db.query(QuizAttempt).filter(
        QuizAttempt.id == attempt_id,
        QuizAttempt.user_id == current_user.id
    ).first()
    
    if not attempt:
        raise HTTPException(status_code=404, detail="Quiz attempt not found.")
        
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.quiz_attempt_id == attempt.id).all()
    
    mcqs_data = []
    selected_answers = {}
    
    for ans in answers:
        mcq = db.query(MCQ).filter(MCQ.id == ans.mcq_id).first()
        if mcq:
            mcqs_data.append({
                "id": mcq.id,
                "main_category": mcq.main_category,
                "sub_category": mcq.sub_category,
                "question_text": mcq.question_text,
                "options": mcq.options,
                "correct_option": mcq.correct_option
            })
            selected_answers[mcq.id] = ans.selected_option
            
    return {
        "id": attempt.id,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
        "score": attempt.score,
        "total_questions": attempt.total_questions,
        "questions": mcqs_data,
        "selected_answers": selected_answers,
        "timer_mode": attempt.timer_mode,
        "timer_value": attempt.timer_value,
        "feedback_mode": attempt.feedback_mode
    }

@router.get("/api/quiz/wrong")
def get_wrong_questions(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Returns the user's previously-missed MCQs (most recently missed first) for drill practice."""
    wrong_answers = (
        db.query(AttemptAnswer)
        .join(QuizAttempt, AttemptAnswer.quiz_attempt_id == QuizAttempt.id)
        .filter(
            QuizAttempt.user_id == current_user.id,
            AttemptAnswer.is_correct.is_(False),
        )
        .order_by(AttemptAnswer.id.desc())
        .limit(200)
        .all()
    )

    mcq_ids: list[int] = []
    seen: set[int] = set()
    for wa in wrong_answers:
        if wa.mcq_id not in seen:
            seen.add(wa.mcq_id)
            mcq_ids.append(wa.mcq_id)

    if not mcq_ids:
        return {"questions": [], "total": 0}

    mcqs = db.query(MCQ).filter(MCQ.id.in_(mcq_ids)).all()
    mcq_map = {m.id: m for m in mcqs}

    questions = []
    for mid in mcq_ids:
        m = mcq_map.get(mid)
        if not m:
            continue
        questions.append({
            "id": m.id,
            "question_text": m.question_text,
            "options": m.options,
            "correct_option": m.correct_option,
            "explanation_markdown": m.explanation_markdown,
            "topic": m.topic,
            "difficulty": m.difficulty,
        })

    return {"questions": questions, "total": len(questions)}
