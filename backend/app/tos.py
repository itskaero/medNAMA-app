"""Table of Specifications (TOS) Engine for medNAMA.

Loads and standardizes official examination blueprints:
- UHS Postgraduate Entrance Test (data/pget_tos.json)
- CPSP FCPS Part 1 (data/fcps_part1_tos.json)

Provides hierarchical blueprint navigation, bank coverage metrics,
topic learning workspace support (concept refresher, revision notes, MCQs),
and TOS-compliant mock exam builders.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import MCQ, AnswerEvent, MCQTag, TopicSummary, User
from app.retention import access_scope
from app.tos_tags import (AXIS_SUBTOPIC, AXIS_TOPIC, paper_prefix, paper_tagged, seed_topic_ids, slug,
                          subtopic_label, topic_condition, topic_label)

logger = logging.getLogger("app.tos")

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _find_data_dir() -> Path:
    candidates = [
        ROOT_DIR / "data",
        Path(__file__).resolve().parent.parent / "data",
        Path(__file__).resolve().parent / "data",
        Path("/app/data"),
        Path("/DATA/mednama/data"),
        Path("data"),
    ]
    for c in candidates:
        if c.exists() and (c / "pget_tos.json").exists():
            return c
    return ROOT_DIR / "data"


DATA_DIR = _find_data_dir()
PGET_TOS_PATH = DATA_DIR / "pget_tos.json"
FCPS_TOS_PATH = DATA_DIR / "fcps_part1_tos.json"
MBBS_TOS_PATH = DATA_DIR / "mbbs_tos.json"


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return cleaned or "default"


class TOSRegistry:
    """In-memory loaded registry of official TOS documents."""

    def __init__(self):
        self.examinations: dict[str, dict[str, Any]] = {}
        self._load_blueprints()

    def _load_blueprints(self):
        self._load_mbbs()
        self._load_pget()
        self._load_fcps1()
        self._finalise()

    def _load_mbbs(self):
        if not MBBS_TOS_PATH.exists():
            logger.warning(f"MBBS TOS file not found at {MBBS_TOS_PATH}")
            return

        try:
            with open(MBBS_TOS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            exam_id = "mbbs"
            exam_info = {
                "id": exam_id,
                "title": data.get("curriculum", "UHS Modular Integrated Curriculum (MBBS 2K23)"),
                "short_name": "MBBS Modular",
                "authority": data.get("authority", "University of Health Sciences Lahore"),
                "version": "UHS Modular Integrated Curriculum 2K23 (Years 1–5)",
                "source": data.get("source") or f"{MBBS_TOS_PATH.name}: UHS MBBS 2K23 block assessment specifications",
                "papers": [],
            }

            years = data.get("years", [])
            for y_idx, y in enumerate(years):
                year_name = y.get("academic_year", f"Year-{y_idx+1}")
                y_slug = f"y{y_idx+1}"

                # 1. Blocks (Years 1 to 4)
                for b in y.get("blocks", []):
                    b_id = b.get("block_id", "Block")
                    b_slug = _slugify(b_id)
                    paper_id = f"{y_slug}-{b_slug}"
                    modules = b.get("modules", [])
                    modules_str = ", ".join(modules) if modules else ""
                    written = b.get("written_exam", {})
                    total_mcqs = written.get("total_mcqs", 140)

                    title = f"{year_name} · {b_id}" + (f" ({modules_str})" if modules_str else "")

                    subjects_list = []
                    for s in b.get("subjects", []):
                        s_name = s.get("subject_name", "").strip()
                        if not s_name:
                            continue
                        mcq_count = s.get("mcq_count")
                        topics_list = []
                        for t in s.get("topics", []):
                            t_name = t.get("topic_name", "").strip()
                            if not t_name:
                                continue
                            subtopics = [st.strip() for st in t.get("subtopics", []) if isinstance(st, str)]
                            topics_list.append({
                                "id": f"{paper_id}/{_slugify(s_name)}/{_slugify(t_name)}",
                                "topic_name": t_name,
                                "subtopics": subtopics,
                            })
                        subjects_list.append({
                            "id": f"{paper_id}/{_slugify(s_name)}",
                            "subject_name": s_name,
                            "official_mcq_count": mcq_count,
                            "topics": topics_list,
                        })

                    exam_info["papers"].append({
                        "id": paper_id,
                        "title": title,
                        "stream": "MBBS",
                        "paper": b_id,
                        "specialty": None,
                        "category": "modular",
                        "total_mcqs": total_mcqs,
                        "subjects": subjects_list,
                    })

                # 2. Clerkships (Year 5)
                for c in y.get("clerkships", []):
                    sp_name = c.get("specialty", "Specialty").strip()
                    sp_slug = _slugify(sp_name)
                    paper_id = f"{y_slug}-{sp_slug}"
                    w_exam = c.get("written_exam", {})
                    total_mcqs = 0
                    for pk, pv in w_exam.items():
                        if isinstance(pv, dict) and "mcq_count" in pv:
                            total_mcqs += pv["mcq_count"]
                    if total_mcqs == 0:
                        total_mcqs = 100

                    title = f"{year_name} · {sp_name}"

                    topics_list = []
                    for t in c.get("topics", []):
                        t_name = t.get("topic_name", "").strip()
                        if not t_name:
                            continue
                        subtopics = [st.strip() for st in t.get("subtopics", []) if isinstance(st, str)]
                        topics_list.append({
                            "id": f"{paper_id}/{_slugify(sp_name)}/{_slugify(t_name)}",
                            "topic_name": t_name,
                            "subtopics": subtopics,
                        })

                    subjects_list = [{
                        "id": f"{paper_id}/{_slugify(sp_name)}",
                        "subject_name": sp_name,
                        "official_mcq_count": total_mcqs,
                        "topics": topics_list,
                    }]

                    exam_info["papers"].append({
                        "id": paper_id,
                        "title": title,
                        "stream": "MBBS",
                        "paper": sp_name,
                        "specialty": sp_name,
                        "category": "clinical",
                        "total_mcqs": total_mcqs,
                        "subjects": subjects_list,
                    })

            self.examinations[exam_id] = exam_info
            logger.info(f"Loaded MBBS Modular TOS with {len(exam_info['papers'])} paper specifications.")
        except Exception as e:
            logger.error(f"Error loading MBBS TOS: {e}", exc_info=True)

    def _load_pget(self):
        if not PGET_TOS_PATH.exists():
            logger.warning(f"PGET TOS file not found at {PGET_TOS_PATH}")
            return

        try:
            with open(PGET_TOS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            exam_id = "pget"
            exam_info = {
                "id": exam_id,
                "title": data.get("examination", "UHS Postgraduate Entrance Test (PGET)"),
                "short_name": "PGET",
                "authority": "University of Health Sciences (UHS) Lahore",
                "version": data.get("version") or "Official 2024–2026 Notification",
                "source": data.get("source") or f"{PGET_TOS_PATH.name}: UHS PGET Table of Specifications, Papers I and II",
                "papers": [],
            }

            specs = data.get("specifications", [])
            for idx, spec in enumerate(specs):
                stream = spec.get("candidate_stream")
                paper_raw = spec.get("paper", "Paper-I")
                specialty_name = spec.get("specialty_name")
                paper_title = spec.get("paper_title")
                total_mcqs = spec.get("total_mcqs", 100)

                if stream == "MBBS" and paper_raw == "Paper-I":
                    paper_id = "mbbs-p1"
                    title = "Paper-I · Basic Medical Sciences (MBBS)"
                    category = "basic"
                    specialty = None
                elif stream == "BDS" and paper_raw == "Paper-I":
                    paper_id = "bds-p1"
                    title = "Paper-I · Basic Medical Sciences (Dental / BDS)"
                    category = "basic"
                    specialty = None
                else:
                    sp_title = specialty_name or paper_title or f"Specialty {idx+1}"
                    paper_id = f"p2-{_slugify(sp_title)}"
                    title = f"Paper-II · {sp_title}"
                    category = "clinical"
                    specialty = sp_title

                subjects_list = []
                for s in spec.get("subjects", []):
                    s_name = s.get("subject_name", "").strip()
                    mcq_count = s.get("mcq_count")
                    topics_list = []
                    for t in s.get("topics", []):
                        t_name = t.get("topic_name", "").strip()
                        subtopics = [st.strip() for st in t.get("subtopics", []) if isinstance(st, str)]
                        topics_list.append({
                            "id": f"{paper_id}/{_slugify(s_name)}/{_slugify(t_name)}",
                            "topic_name": t_name,
                            "subtopics": subtopics,
                            "min_mcqs": t.get("min_mcqs"),
                        })
                    subjects_list.append({
                        "id": f"{paper_id}/{_slugify(s_name)}",
                        "subject_name": s_name,
                        "official_mcq_count": mcq_count,
                        "topics": topics_list,
                    })

                exam_info["papers"].append({
                    "id": paper_id,
                    "title": title,
                    "stream": stream,
                    "paper": paper_raw,
                    "specialty": specialty,
                    "category": category,
                    "total_mcqs": total_mcqs,
                    "subjects": subjects_list,
                })

            self.examinations[exam_id] = exam_info
            logger.info(f"Loaded PGET TOS with {len(exam_info['papers'])} paper specifications.")
        except Exception as e:
            logger.error(f"Error loading PGET TOS: {e}", exc_info=True)

    def _load_fcps1(self):
        if not FCPS_TOS_PATH.exists():
            logger.warning(f"FCPS-I TOS file not found at {FCPS_TOS_PATH}")
            return

        try:
            with open(FCPS_TOS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            exam_id = "fcps1"
            exam_info = {
                "id": exam_id,
                "title": "CPSP FCPS Part 1 Examination",
                "short_name": "FCPS-I",
                "authority": "College of Physicians and Surgeons Pakistan (CPSP)",
                "version": data.get("version") or "Official CPSP Part-I Syllabus",
                "source": data.get("source") or f"{FCPS_TOS_PATH.name}: CPSP FCPS Part I prospectus, per specialty",
                "papers": [],
            }

            specialties = data.get("specialties", [])
            for sp in specialties:
                sp_name = sp.get("specialty_name", "General")
                sp_slug = _slugify(sp_name)
                syllabus = sp.get("syllabus", {})

                # Paper 1
                p1 = syllabus.get("paper_1", {})
                if p1:
                    subjects_list = []
                    for s in p1.get("subjects", []):
                        s_name = s.get("subject_name", "").strip()
                        topics_list = []
                        for sub_sub in s.get("sub_subjects", []):
                            sub_sub_name = sub_sub.get("sub_subject_name", "").strip()
                            raw_topics = sub_sub.get("topics", [])
                            topics_list.append({
                                "min_mcqs": sub_sub.get("min_mcqs"),
                                "id": f"fcps1-{sp_slug}-p1/{_slugify(s_name)}/{_slugify(sub_sub_name)}",
                                "topic_name": sub_sub_name,
                                "subtopics": raw_topics,
                            })
                        subjects_list.append({
                            "id": f"fcps1-{sp_slug}-p1/{_slugify(s_name)}",
                            "subject_name": s_name,
                            "official_mcq_count": s.get("mcq_count") or None,
                            "topics": topics_list,
                        })

                    exam_info["papers"].append({
                        "id": f"{sp_slug}-p1",
                        "title": f"{sp_name} · Paper 1 (Basic Sciences)",
                        "specialty": sp_name,
                        "paper": "Paper 1",
                        "category": "basic",
                        "total_mcqs": 100,
                        "subjects": subjects_list,
                    })

                # Paper 2
                p2 = syllabus.get("paper_2", {})
                if p2:
                    subjects_list = []
                    for s in p2.get("subjects", []):
                        s_name = s.get("subject_name", "").strip()
                        topics_list = []
                        for sub_sub in s.get("sub_subjects", []):
                            sub_sub_name = sub_sub.get("sub_subject_name", "").strip()
                            raw_topics = sub_sub.get("topics", [])
                            topics_list.append({
                                "min_mcqs": sub_sub.get("min_mcqs"),
                                "id": f"fcps1-{sp_slug}-p2/{_slugify(s_name)}/{_slugify(sub_sub_name)}",
                                "topic_name": sub_sub_name,
                                "subtopics": raw_topics,
                            })
                        subjects_list.append({
                            "id": f"fcps1-{sp_slug}-p2/{_slugify(s_name)}",
                            "subject_name": s_name,
                            "official_mcq_count": s.get("mcq_count") or None,
                            "topics": topics_list,
                        })

                    exam_info["papers"].append({
                        "id": f"{sp_slug}-p2",
                        "title": f"{sp_name} · Paper 2 (Specialty & Clinical)",
                        "specialty": sp_name,
                        "paper": "Paper 2",
                        "category": "clinical",
                        "total_mcqs": 100,
                        "subjects": subjects_list,
                    })

            self.examinations[exam_id] = exam_info
            logger.info(f"Loaded FCPS Part 1 TOS with {len(exam_info['papers'])} paper specifications.")
        except Exception as e:
            logger.error(f"Error loading FCPS-I TOS: {e}", exc_info=True)

    def _finalise(self):
        """Per paper: does the TOS print subject counts (weighted), and does it list any topic (available)?"""
        for exam in self.examinations.values():
            for p in exam.get("papers", []):
                subs = p.get("subjects", [])
                p["weighted"] = any(s.get("official_mcq_count") for s in subs)
                p["available"] = any(s.get("topics") for s in subs)

    def get_examinations_summary(self) -> list[dict[str, Any]]:
        """Returns high-level metadata for examination selectors."""
        out = []
        for eid, exam in self.examinations.items():
            papers_summary = [
                {
                    "id": p["id"],
                    "title": p["title"],
                    "specialty": p.get("specialty"),
                    "paper": p.get("paper"),
                    "category": p.get("category"),
                    "total_mcqs": p.get("total_mcqs", 100),
                    "subjects_count": len(p.get("subjects", [])),
                    "weighted": p.get("weighted", False),
                    "available": p.get("available", True),
                }
                for p in exam.get("papers", [])
            ]
            out.append({
                "id": exam["id"],
                "title": exam["title"],
                "short_name": exam["short_name"],
                "authority": exam["authority"],
                "version": exam["version"],
                "source": exam.get("source"),
                "papers": papers_summary,
            })
        return out

    def get_paper(self, exam_id: str, paper_id: str) -> dict[str, Any] | None:
        exam = self.examinations.get(exam_id)
        if not exam:
            return None
        for p in exam.get("papers", []):
            if p["id"] == paper_id:
                return p
        return None


# Global registry singleton
tos_registry = TOSRegistry()


# ─── Database Metrics & Query Helpers ─────────────────────────────────────────
#
# Every count and every question below comes from the TOS tags (app/tos_tags.py, written by
# scripts/tag_tos.py), never from string matching at request time. Three numbers are kept apart and
# named so the screen cannot mix them up:
#   official_mcq_count  what the published TOS says (None when it prints no number)
#   bank_mcq_count      distinct ready questions tagged to the node that this user may see
#   attempted/correct   this user's recorded answers (answer_events) on those questions

MINI_QUOTA = 20            # internal mini-mock size for a subject with no official count
MINI_MIN_MINUTES = 10
ADAPTIVE_MIN_ANSWERS = 8   # answers on a topic before difficulty adapts to the learner
MASTERY_MIN_ANSWERS = 20   # fewer answers than this is "observed accuracy", never "mastery"


def find_topic(paper: dict[str, Any], topic_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for s in paper.get("subjects", []):
        for t in s.get("topics", []):
            if t["id"] == topic_id:
                return s, t
    return None


def find_subject(paper: dict[str, Any], subject_id: str) -> dict[str, Any] | None:
    for s in paper.get("subjects", []):
        if s["id"] == subject_id or s["subject_name"].lower() == (subject_id or "").lower():
            return s
    return None


def _visible(db: Session, user: User | None):
    """Ready questions this user may see (restricted past papers only with access)."""
    return access_scope(db.query(MCQ).filter(MCQ.status == "ready", MCQ.twist_of.is_(None)), user)


def _label_counts(db: Session, user: User | None, axis: str, prefix: str) -> dict[str, int]:
    q = (_visible(db, user).join(MCQTag, MCQTag.mcq_id == MCQ.id)
         .filter(MCQTag.axis == axis, MCQTag.label.like(_like_prefix(prefix), escape="\\"))
         .with_entities(MCQTag.label, func.count(func.distinct(MCQ.id))).group_by(MCQTag.label))
    return {label: n for label, n in q.all()}


def _like_prefix(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def _user_label_stats(db: Session, user_id: int, axis: str, prefix: str) -> dict[str, dict[str, int]]:
    """Per label: answers, correct answers and distinct questions answered by this user."""
    rows = db.execute(text(
        "SELECT t.label, count(*) AS n, sum(CASE WHEN e.is_correct THEN 1 ELSE 0 END) AS c, "
        "count(DISTINCT e.mcq_id) AS q "
        "FROM answer_events e JOIN mcq_tags t ON t.mcq_id = e.mcq_id "
        "WHERE e.user_id = :u AND t.axis = :a AND t.label LIKE :p ESCAPE '\\' GROUP BY t.label"),
        {"u": user_id, "a": axis, "p": _like_prefix(prefix)}).fetchall()
    return {r[0]: {"answers": int(r[1]), "correct": int(r[2] or 0), "questions": int(r[3])} for r in rows}


def _accuracy(stats: dict[str, int] | None) -> float | None:
    if not stats or not stats.get("answers"):
        return None
    return round(stats["correct"] / stats["answers"] * 100, 1)


def _notes_by_subtopic(db: Session, exam_id: str, paper: dict[str, Any]) -> dict[str, set[str]]:
    """topic id -> subtopics with a finished Seed Studio revision note."""
    nodes = seed_topic_ids(db, exam_id, paper)
    if not nodes:
        return {}
    try:
        rows = db.execute(text("SELECT DISTINCT node_id FROM seed_notes WHERE status = 'done' AND node_id = ANY(:ids)"),
                          {"ids": list(nodes)}).fetchall()
    except Exception:
        db.rollback()
        return {}
    out: dict[str, set[str]] = {}
    for (nid,) in rows:
        topic_id, sub = nodes[nid]
        out.setdefault(topic_id, set()).add(sub or "")   # "" = a note on the topic itself (no subtopics)
    return out


def get_paper_tree_with_metrics(db: Session, exam_id: str, paper_id: str, user: User | None = None) -> dict[str, Any] | None:
    """The paper's blueprint with, per subject/topic/subtopic: official count, bank count, the user's answers."""
    paper = tos_registry.get_paper(exam_id, paper_id)
    if not paper:
        return None
    exam = tos_registry.examinations[exam_id]
    prefix = paper_prefix(exam_id, paper_id)
    tagged = paper_tagged(db, exam_id, paper_id)
    topic_counts = _label_counts(db, user, AXIS_TOPIC, prefix) if tagged else {}
    sub_counts = _label_counts(db, user, AXIS_SUBTOPIC, prefix) if tagged else {}
    topic_user = _user_label_stats(db, user.id, AXIS_TOPIC, prefix) if (user and tagged) else {}
    sub_user = _user_label_stats(db, user.id, AXIS_SUBTOPIC, prefix) if (user and tagged) else {}
    cached = {k for (k,) in db.query(TopicSummary.scope_key)
              .filter(TopicSummary.scope_key.like(_like_prefix(f"tos:{exam_id}:{paper_id}:"), escape="\\")).all()}
    seed_notes = _notes_by_subtopic(db, exam_id, paper)

    subjects_out = []
    for subj in paper.get("subjects", []):
        topics_out = []
        s_label_prefix = f"{exam_id}:{subj['id']}/"
        for top in subj.get("topics", []):
            label = topic_label(exam_id, top["id"])
            ustats = topic_user.get(label)
            noted_subs = seed_notes.get(top["id"], set())
            subtopics = []
            for st in top.get("subtopics", []):
                sl = subtopic_label(exam_id, top["id"], st)
                subtopics.append({
                    "name": st,
                    "bank_mcq_count": sub_counts.get(sl, 0) if tagged else None,
                    "has_notes": st in noted_subs or f"tos:{exam_id}:{paper_id}:{top['id']}#{slug(st)}" in cached,
                    "accuracy": _accuracy(sub_user.get(sl)),
                })
            topics_out.append({
                "id": top["id"],
                "topic_name": top["topic_name"],
                "subtopics": top.get("subtopics", []),
                "subtopic_details": subtopics,
                "min_mcqs": top.get("min_mcqs"),
                "bank_mcq_count": topic_counts.get(label, 0) if tagged else None,
                "attempted_count": ustats["answers"] if ustats else 0,
                "questions_answered": ustats["questions"] if ustats else 0,
                "correct_count": ustats["correct"] if ustats else 0,
                "accuracy": _accuracy(ustats),
                "mastery_estimable": bool(ustats and ustats["answers"] >= MASTERY_MIN_ANSWERS),
                "has_notes": f"tos:{exam_id}:{paper_id}:{top['id']}" in cached or bool(noted_subs),
            })
        # Subject bank: distinct questions over its topics (a question sits in one topic per paper).
        subj_bank = sum(n for lbl, n in topic_counts.items() if lbl.startswith(s_label_prefix)) if tagged else None
        s_stats = [topic_user[k] for k in topic_user if k.startswith(s_label_prefix)]
        s_answers = sum(x["answers"] for x in s_stats)
        s_correct = sum(x["correct"] for x in s_stats)
        covered = sum(1 for t in topics_out if (t["bank_mcq_count"] or 0) > 0)
        subjects_out.append({
            "id": subj["id"],
            "subject_name": subj["subject_name"],
            "official_mcq_count": subj.get("official_mcq_count"),
            "bank_mcq_count": subj_bank,
            "attempted_count": s_answers,
            "correct_count": s_correct,
            "accuracy": round(s_correct / s_answers * 100, 1) if s_answers else None,
            "topics_with_questions": covered if tagged else None,
            "topics": topics_out,
        })

    all_answers = sum(s["attempted_count"] for s in subjects_out)
    all_correct = sum(s["correct_count"] for s in subjects_out)
    return {
        "id": paper["id"],
        "title": paper["title"],
        "stream": paper.get("stream"),
        "paper": paper.get("paper"),
        "specialty": paper.get("specialty"),
        "category": paper.get("category"),
        "total_mcqs": paper.get("total_mcqs", 100),
        "weighted": paper.get("weighted", False),
        "available": paper.get("available", True),
        "source": exam.get("source"),
        "version": exam.get("version"),
        "tagged": tagged,
        "bank_mcq_count": sum(s["bank_mcq_count"] or 0 for s in subjects_out) if tagged else None,
        "attempted_count": all_answers,
        "accuracy": round(all_correct / all_answers * 100, 1) if all_answers else None,
        "subjects": subjects_out,
    }


def _difficulty_band(db: Session, user: User | None, exam_id: str, topic_id: str) -> tuple[list[int] | None, str]:
    """Adaptive difficulty from this learner's answers on the topic; mixed until there are enough."""
    if user is None:
        return None, "mixed (sign in to adapt)"
    st = _user_label_stats(db, user.id, AXIS_TOPIC, topic_label(exam_id, topic_id)).get(topic_label(exam_id, topic_id))
    if not st or st["answers"] < ADAPTIVE_MIN_ANSWERS:
        n = st["answers"] if st else 0
        return None, f"mixed: adapts after {ADAPTIVE_MIN_ANSWERS} answers on this topic ({n} so far)"
    acc = st["correct"] / st["answers"]
    if acc >= 0.8:
        return [4, 5], f"levels 4–5 (you score {acc:.0%} here)"
    if acc >= 0.6:
        return [3, 4], f"levels 3–4 (you score {acc:.0%} here)"
    return [1, 2, 3], f"levels 1–3 (you score {acc:.0%} here)"


def fetch_topic_mcqs(db: Session, user: User | None, exam_id: str, paper_id: str, topic_id: str,
                     subtopic: str | None = None, difficulty: int | str | None = None,
                     limit: int = 10) -> dict[str, Any]:
    """Questions tagged to this topic (or subtopic) only. Never tops up from other topics: when fewer
    exist than asked for, `available` says so."""
    cond = topic_condition(exam_id, topic_id, subtopic)
    base = _visible(db, user).filter(cond)
    adaptive_note = None
    levels: list[int] | None = None
    if difficulty == "adaptive":
        levels, adaptive_note = _difficulty_band(db, user, exam_id, topic_id)
    elif isinstance(difficulty, int) or (isinstance(difficulty, str) and difficulty.isdigit()):
        levels = [int(difficulty)]
    q = base
    if levels:
        q = q.filter(func.coalesce(MCQ.difficulty, 3).in_(levels))
    available = q.with_entities(func.count(func.distinct(MCQ.id))).scalar() or 0

    seen: set[int] = set()
    if user is not None:
        seen = {r[0] for r in db.query(AnswerEvent.mcq_id).filter(AnswerEvent.user_id == user.id,
                                                                  AnswerEvent.mcq_id.isnot(None)).distinct()}
    rows = q.order_by(func.random()).limit(max(limit * 4, 40)).all()
    rows.sort(key=lambda m: m.id in seen)          # unseen first, random within each group
    picked, groups = [], set()
    for m in rows:
        key = m.recall_group if m.recall_group is not None else -m.id
        if key in groups:
            continue
        groups.add(key)
        picked.append(m)
        if len(picked) >= limit:
            break
    return {
        "questions": [_format_mcq(m) for m in picked],
        "available": available,
        "requested": limit,
        "levels": levels,
        "adaptive_note": adaptive_note,
    }


def _fuzzy_match(s1: str, s2: str) -> bool:
    """Check if keywords in s1 match s2."""
    words1 = set(re.findall(r"\w{4,}", s1.lower()))
    words2 = set(re.findall(r"\w{4,}", s2.lower()))
    return bool(words1 and words2 and (words1 & words2))


def _format_mcq(m: MCQ) -> dict[str, Any]:
    grounding = m.grounding or ("book" if m.book_id else None)
    if m.main_category and m.main_category.startswith("Past papers"):
        provenance = "Past paper (recalled)"
    elif m.source == "seed-studio" or (m.main_category or "").startswith("Seed"):
        provenance = "Validated seed bank"
    elif m.main_category == "AI MCQs":
        provenance = "AI-written, textbook-grounded" if grounding == "book" else "AI-written (no textbook support found)"
    else:
        provenance = m.main_category or "Question bank"
    return {
        "id": m.id,
        "question_text": m.question_text,
        "options": m.options,
        "correct_option": m.correct_option,
        "topic": m.topic,
        "main_category": m.main_category,
        "sub_category": m.sub_category,
        "difficulty": m.difficulty or 3,
        "explanation_markdown": m.explanation_markdown or "",
        "explanation_citations": m.explanation_citations or {},
        "source": provenance,
        "grounding": grounding,
        "media": [],
        "figure_id": m.figure_id,
    }


# ─── TOS-aware mocks ──────────────────────────────────────────────────────────

def mini_minutes(n: int) -> int:
    return max(MINI_MIN_MINUTES, round(n * 1.2))


def full_minutes(n: int) -> int:
    return 120 if n >= 100 else max(MINI_MIN_MINUTES, round(n * 1.2))


def _allocate(paper: dict[str, Any], subject: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Questions each subject should get, and where the number comes from.

    official   printed in the TOS
    internal   our rule, used only where the TOS prints nothing: the rest of the paper's total split by
               number of topics (unweighted papers), or MINI_QUOTA for a mini-mock
    """
    subjects = [subject] if subject else paper.get("subjects", [])
    if subject:
        oc = subject.get("official_mcq_count")
        return [{"subject": subject, "quota": oc or MINI_QUOTA, "source": "official" if oc else "internal"}]
    total = int(paper.get("total_mcqs") or 100)
    fixed = sum(s.get("official_mcq_count") or 0 for s in subjects)
    free = [s for s in subjects if not s.get("official_mcq_count")]
    rest = max(0, total - fixed)
    weights = [max(1, len(s.get("topics", []))) for s in free]
    shares = _split(rest, weights)
    out = []
    for s in subjects:
        if s.get("official_mcq_count"):
            out.append({"subject": s, "quota": int(s["official_mcq_count"]), "source": "official"})
        else:
            out.append({"subject": s, "quota": shares[free.index(s)], "source": "internal"})
    return out


def _split(n: int, weights: list[int]) -> list[int]:
    """n split in proportion to weights, largest remainders first, summing to exactly n."""
    if not weights:
        return []
    tot = sum(weights)
    raw = [n * w / tot for w in weights]
    base = [int(x) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)[: n - sum(base)]:
        base[i] += 1
    return base


def plan_mock(db: Session, user: User | None, exam_id: str, paper_id: str, mini_subject: str | None = None,
              pick: bool = False) -> dict[str, Any]:
    """The blueprint allocation for a full paper or one subject's mini-mock, what the bank can supply, and
    (with pick=True) the questions: spread over each subject's topics, topic minimums first, no question
    or recalled-twin twice, unseen questions first."""
    paper = tos_registry.get_paper(exam_id, paper_id)
    if not paper:
        return {"error": "Paper not found"}
    subject = find_subject(paper, mini_subject) if mini_subject else None
    if mini_subject and subject is None:
        return {"error": f"No subject '{mini_subject}' in this paper"}
    tagged = paper_tagged(db, exam_id, paper_id)
    prefix = paper_prefix(exam_id, paper_id)
    counts = _label_counts(db, user, AXIS_TOPIC, prefix) if tagged else {}
    seen: set[int] = set()
    if pick and user is not None:
        seen = {r[0] for r in db.query(AnswerEvent.mcq_id).filter(AnswerEvent.user_id == user.id,
                                                                  AnswerEvent.mcq_id.isnot(None)).distinct()}
    taken_ids: set[int] = set()
    taken_groups: set[int] = set()
    picked: list[int] = []
    rows_out = []
    deviations = []
    for alloc in _allocate(paper, subject):
        s = alloc["subject"]
        topics = s.get("topics", [])
        available = sum(counts.get(topic_label(exam_id, t["id"]), 0) for t in topics)
        quota = alloc["quota"]
        got: list[int] = []
        if pick and topics and quota:
            pools: dict[str, list[MCQ]] = {}
            for t in topics:
                rows = (_visible(db, user).filter(topic_condition(exam_id, t["id"]))
                        .with_entities(MCQ.id, MCQ.recall_group).order_by(func.random()).limit(quota * 3).all())
                rows.sort(key=lambda r: r[0] in seen)
                pools[t["id"]] = rows

            def take(topic_id: str) -> bool:
                pool = pools[topic_id]
                while pool:
                    mid, grp = pool.pop(0)
                    g = grp if grp is not None else -mid
                    if mid in taken_ids or g in taken_groups:
                        continue
                    taken_ids.add(mid)
                    taken_groups.add(g)
                    got.append(mid)
                    return True
                return False

            for t in topics:                       # explicit topic minimums first
                for _ in range(int(t.get("min_mcqs") or 0)):
                    if len(got) < quota:
                        take(t["id"])
            while len(got) < quota:                # then round-robin across topics
                progressed = False
                for t in topics:
                    if len(got) >= quota:
                        break
                    progressed = take(t["id"]) or progressed
                if not progressed:
                    break
            picked.extend(got)
        supplied = len(got) if pick else min(quota, available)
        short = max(0, quota - supplied)
        if short:
            deviations.append(f"{s['subject_name']}: {supplied} of {quota} ({short} short in the bank)")
        rows_out.append({
            "subject_id": s["id"],
            "subject_name": s["subject_name"],
            "official_quota": s.get("official_mcq_count"),
            "quota": quota,
            "quota_source": alloc["source"],
            "available_in_bank": available,
            "supplied": supplied,
            "sufficient": short == 0,
            "deviation": short,
            "mini_questions": min(s.get("official_mcq_count") or MINI_QUOTA, available),
            "mini_minutes": mini_minutes(min(s.get("official_mcq_count") or MINI_QUOTA, available)),
        })
    total_quota = sum(r["quota"] for r in rows_out)
    total_supplied = sum(r["supplied"] for r in rows_out)
    weighted = bool(paper.get("weighted"))
    internal = [r["subject_name"] for r in rows_out if r["quota_source"] == "internal"]
    return {
        "exam_id": exam_id,
        "paper_id": paper_id,
        "title": paper["title"],
        "mini_subject": subject["subject_name"] if subject else None,
        "tagged": tagged,
        "weighted": weighted,
        "official_total_mcqs": paper.get("total_mcqs", 100),
        "bank_total_available": sum(r["available_in_bank"] for r in rows_out),
        "planned_questions": total_quota,
        "supplied_questions": total_supplied,
        "minutes": mini_minutes(total_supplied) if subject else full_minutes(total_supplied),
        # Compliant only when the TOS prints the counts and the bank meets every one of them.
        "is_compliant": bool(weighted and tagged and not deviations and not internal),
        "compliance_note": (
            "Not mapped yet: run scripts/tag_tos.py." if not tagged else
            "This TOS prints no question counts, so the allocation is ours (by number of topics), not official."
            if not weighted else
            ("Some subjects have no printed count; their share is ours: " + ", ".join(internal)) if internal else
            ("The bank cannot meet every official count." if deviations else "Matches the official distribution.")),
        "deviations": deviations,
        "quotas": rows_out,
        "mcq_ids": picked if pick else None,
    }


def audit_mock_distribution(db: Session, exam_id: str, paper_id: str, user: User | None = None) -> dict[str, Any]:
    return plan_mock(db, user, exam_id, paper_id)
